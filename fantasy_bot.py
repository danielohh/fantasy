#!/usr/bin/env python3

import argparse
import datetime
import io
import os
import smtplib
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from client import get_league
from analyzer import analyze


# ---------------------------------------------------------------------------
# Email utilities
# ---------------------------------------------------------------------------

def _send_email(subject, body, to_addr=None):
    """Send email with subject and body. Uses Gmail by default."""
    if to_addr is None:
        to_addr = os.environ.get('EMAIL_TO')
    if not to_addr:
        raise ValueError('EMAIL_TO not set. Set EMAIL_TO env var or pass --email recipient@example.com')

    smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    smtp_user = os.environ.get('SMTP_USER')
    smtp_password = os.environ.get('SMTP_PASSWORD')

    if not smtp_user or not smtp_password:
        raise ValueError('SMTP_USER and SMTP_PASSWORD required. Set env vars for email authentication.')

    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = to_addr
    msg.attach(MIMEText(body, 'plain'))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        print(f"✓ Email sent to {to_addr}")
    except Exception as e:
        print(f"✗ Failed to send email: {e}", file=sys.stderr)
        raise


def _capture_output(func, *args, **kwargs):
    """Capture stdout from a function and return it as a string."""
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        func(*args, **kwargs)
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    return output


# ---------------------------------------------------------------------------
# Transaction / roster commands
# ---------------------------------------------------------------------------

def cmd_roster(args):
    lg = get_league(cache=args.cache)
    tm = lg.to_team(lg.team_key())
    roster = tm.roster(day=args.date)
    print(f"{'ID':<8} {'Name':<25} {'Pos':<6} Eligible")
    print("-" * 60)
    for p in roster:
        eligible = ','.join(p['eligible_positions'])
        status   = f" [{p['status']}]" if p.get('status') else ''
        print(f"{p['player_id']:<8} {p['name']:<25} {p['selected_position']:<6} {eligible}{status}")


def cmd_lineup(args):
    lg = get_league(cache=args.cache)
    tm = lg.to_team(lg.team_key())
    changes = []
    for entry in args.set:
        player_id, position = entry.split(':')
        changes.append({'player_id': int(player_id), 'selected_position': position})
    tm.change_positions(args.date, changes)
    print(f"Lineup updated for {args.date}.")


def cmd_free_agents(args):
    lg = get_league(cache=args.cache)
    players = lg.free_agents(args.position)
    print(f"{'ID':<8} {'Name':<25} {'%Own':<6} Eligible")
    print("-" * 55)
    for p in players:
        eligible = ','.join(p['eligible_positions'])
        pct      = str(p.get('percent_owned', '')) + '%'
        print(f"{p['player_id']:<8} {p['name']:<25} {pct:<6} {eligible}")


def cmd_waivers(args):
    lg = get_league(cache=args.cache)
    players = lg.waivers()
    print(f"{'ID':<8} {'Name':<25} {'%Own':<6} {'Status':<8} Eligible")
    print("-" * 65)
    for p in players:
        pct      = str(p.get('percent_owned', '')) + '%'
        status   = p.get('status', '')
        eligible = ','.join(p.get('eligible_positions', []))
        print(f"{p['player_id']:<8} {p['name']:<25} {pct:<6} {status:<8} {eligible}")


def cmd_add(args):
    lg = get_league(cache=args.cache)
    tm = lg.to_team(lg.team_key())
    tm.add_player(args.player_id)
    print(f"Added player {args.player_id}.")


def cmd_drop(args):
    lg = get_league(cache=args.cache)
    tm = lg.to_team(lg.team_key())
    tm.drop_player(args.player_id)
    print(f"Dropped player {args.player_id}.")


def cmd_add_drop(args):
    lg = get_league(cache=args.cache)
    tm = lg.to_team(lg.team_key())
    tm.add_and_drop_players(args.add_id, args.drop_id)
    print(f"Added {args.add_id}, dropped {args.drop_id}.")


def cmd_claim(args):
    lg = get_league(cache=args.cache)
    tm = lg.to_team(lg.team_key())
    tm.claim_and_drop_players(args.add_id, args.drop_id)
    print(f"Waiver claim submitted: add {args.add_id}, drop {args.drop_id}.")


# ---------------------------------------------------------------------------
# Analyze command + formatters
# ---------------------------------------------------------------------------

def cmd_clear_cache(_args):
    import os
    from client import CACHE_FILE as YAHOO_CACHE
    from mlb_stats import MLB_CACHE_FILE as MLB_CACHE
    for path in (YAHOO_CACHE, MLB_CACHE):
        if os.path.exists(path):
            os.remove(path)
            print(f"Deleted {path}.")
        else:
            print(f"No cache file at {path}.")


def _progress(msg):
    print(f"  > {msg}", flush=True)


def cmd_analyze(args):
    lg      = get_league()
    section = args.section
    days    = args.days
    results = analyze(lg, section=section, days=days, progress=_progress)
    print()  # blank line before results

    if 'injuries' in results:
        _print_injuries(results['injuries'])
    if 'streaming' in results:
        _print_streaming(results['streaming'])
    if 'waivers' in results:
        _print_waivers(results['waivers'])
    if 'waiver_pitchers' in results:
        _print_waiver_pitchers(results['waiver_pitchers'])
    if 'categories' in results:
        _print_categories(results['categories'])
    if 'news' in results:
        _print_news(results['news'])


def cmd_advise(args):
    import os

    lg      = get_league(cache=args.cache)
    results = analyze(lg, progress=_progress)
    print()

    _header("AI ADVISOR")
    prompt = _build_advise_prompt(results)

    if os.environ.get('ANTHROPIC_API_KEY'):
        _advise_claude(prompt)
    elif os.environ.get('GOOGLE_API_KEY'):
        _advise_gemini(prompt)
    else:
        print("  Set ANTHROPIC_API_KEY or GOOGLE_API_KEY to enable AI advice.")


def cmd_email_report(args):
    """Run analyze + advise and email the report."""
    to_addr = args.email if hasattr(args, 'email') and args.email else None

    lg = get_league(cache=args.cache)
    results = analyze(lg, progress=None)

    lines = [f"Fantasy Baseball Daily Report — {datetime.date.today()}\n"]

    # Streaming pitchers
    streaming = results.get('streaming', {})
    targets = streaming.get('targets', []) if isinstance(streaming, dict) else []
    lines.append("\nSTREAMING TARGETS (next 3 days):")
    if targets:
        lines.append("-" * 70)
        for t in targets[:5]:
            lines.append(
                f"{t['name']:<20} {t['opp']:<5} "
                f"ERA {t['stats'].get('era', 0):>5.2f}  "
                f"WHIP {t['stats'].get('whip', 0):>5.2f}  "
                f"K/9 {t['stats'].get('k9', 0):>5.2f}"
            )
    else:
        lines.append("  None available")

    # Waiver pitchers
    waiver_p = results.get('waiver_pitchers', [])
    lines.append("\nWAIVER TARGETS (top RPs):")
    if waiver_p:
        lines.append("-" * 70)
        for p in waiver_p[:5]:
            lines.append(
                f"{p['name']:<20} {p.get('source', 'W'):<3} "
                f"SV+HLD {p['sv_hld']:>2}  "
                f"ERA {p['stats'].get('era', 0):>5.2f}  "
                f"WHIP {p['stats'].get('whip', 0):>5.2f}"
            )
    else:
        lines.append("  None available")

    # News & transactions
    news = results.get('news', {})
    transactions = news.get('transactions', []) if isinstance(news, dict) else []
    lines.append("\nRECENT TRANSACTIONS:")
    if transactions:
        lines.append("-" * 70)
        for t in transactions[:5]:
            team_str = f" ({t['team']})" if t.get('team') else ""
            lines.append(f"{t['date']} | {t['type']:<20} | {t['player']}{team_str}")
    else:
        lines.append("  None")

    # Roster schedule
    roster_schedule = news.get('roster_schedule', []) if isinstance(news, dict) else []
    lines.append("\nROSTER SCHEDULE (most games):")
    if roster_schedule:
        lines.append("-" * 70)
        for p in roster_schedule[:5]:
            lines.append(f"{p['name']:<20} ({p['team']:<4}) — {p['games']} games")
    else:
        lines.append("  None")

    # Get AI advisor (call LLM directly to avoid duplicate analyze)
    lines.append("\n" + "=" * 70)
    lines.append("AI ADVISOR RECOMMENDATIONS:")
    lines.append("=" * 70)

    prompt = _build_advise_prompt(results)
    try:
        if os.environ.get('ANTHROPIC_API_KEY'):
            advice = _get_advise_text_claude(prompt)
        elif os.environ.get('GOOGLE_API_KEY'):
            advice = _get_advise_text_gemini(prompt)
        else:
            advice = "Set ANTHROPIC_API_KEY or GOOGLE_API_KEY to enable AI advice."
        lines.append(advice)
    except Exception as e:
        lines.append(f"Error generating advice: {e}")

    body = '\n'.join(lines)
    subject = f"Fantasy Baseball — {datetime.date.today().strftime('%a, %b %d')}"
    _send_email(subject, body, to_addr)


def _advise_claude(prompt):
    import anthropic
    client = anthropic.Anthropic()
    with client.messages.stream(
        model='claude-opus-4-6',
        max_tokens=4096,
        thinking={'type': 'adaptive'},
        system='You are a concise fantasy baseball expert. Give actionable advice only.',
        messages=[{'role': 'user', 'content': prompt}],
    ) as stream:
        for text in stream.text_stream:
            print(text, end='', flush=True)
    print()


def _advise_gemini(prompt):
    from google import genai
    from google.genai import types
    import os
    client = genai.Client(api_key=os.environ['GOOGLE_API_KEY'])
    full_prompt = 'You are a concise fantasy baseball expert. Give actionable advice only.\n\n' + prompt
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )
    for chunk in client.models.generate_content_stream(
        model='gemini-2.5-flash', contents=full_prompt, config=config,
    ):
        if chunk.text:
            print(chunk.text, end='', flush=True)
    print()


def _get_advise_text_claude(prompt):
    """Get advice text from Claude without printing."""
    import anthropic
    client = anthropic.Anthropic()
    with client.messages.stream(
        model='claude-opus-4-6',
        max_tokens=4096,
        thinking={'type': 'adaptive'},
        system='You are a concise fantasy baseball expert. Give actionable advice only.',
        messages=[{'role': 'user', 'content': prompt}],
    ) as stream:
        return stream.get_final_message().content[0].text


def _get_advise_text_gemini(prompt):
    """Get advice text from Gemini without printing."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ['GOOGLE_API_KEY'])
    full_prompt = 'You are a concise fantasy baseball expert. Give actionable advice only.\n\n' + prompt
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )
    response = client.models.generate_content(
        model='gemini-2.5-flash', contents=full_prompt, config=config,
    )
    return response.text


def _build_advise_prompt(results):
    lines = []

    injuries = results.get('injuries', [])
    if injuries:
        lines.append('INJURY ALERTS:')
        for a in injuries:
            lines.append(f"  {a['player_name']} ({a['status']}) in slot {a['current_slot']} — {a['action']}")
    else:
        lines.append('INJURY ALERTS: None')

    streaming = results.get('streaming', {})
    if isinstance(streaming, dict):
        targets = streaming.get('targets', [])[:5]
        drops   = streaming.get('drop_candidates', [])
        if targets:
            lines.append('\nSTREAMING TARGETS (top 5):')
            for p in targets:
                s     = p.get('stats', {})
                extra = f" +{len(p['extra_starts'])} more" if p.get('extra_starts') else ''
                ha    = 'home' if p.get('home') else 'away'
                lines.append(
                    f"  {p['player_name']} ({p['source']}) starts {p['start_date']} "
                    f"vs {p['opponent']} ({ha}){extra} | ERA {s.get('era','--')} "
                    f"WHIP {s.get('whip','--')} K/9 {s.get('k9','--')}"
                )
        if drops:
            lines.append('  My active SPs with no start in window:')
            for d in drops:
                lines.append(f"    {d['player_name']} (slot {d['slot']})")

    waiver_pitchers = results.get('waiver_pitchers', [])
    if waiver_pitchers:
        lines.append('\nWAIVER/FA RELIEVER TARGETS:')
        for p in waiver_pitchers[:5]:
            s = p['stats']
            lines.append(
                f"  {p['name']} ({p['source']}, {p['percent_owned']}% owned) — "
                f"SV {s.get('saves',0)} HLD {s.get('holds',0)} "
                f"ERA {s.get('era','--')} WHIP {s.get('whip','--')}"
            )

    waivers = results.get('waivers', {})
    if waivers:
        lines.append('\nWAIVER/FA BATTER TARGETS:')
        for pos, upgrades in waivers.items():
            for u in upgrades[:2]:
                c  = u['candidate']
                cs = c.get('stats', {})
                r  = u.get('replaces')
                lines.append(
                    f"  [{pos}] ADD {c['name']} ({c.get('percent_owned',0)}% owned) — "
                    f"AVG {cs.get('avg','--')} OBP {cs.get('obp','--')} SLG {cs.get('slg','--')} "
                    f"HR {cs.get('hr','--')} SB {cs.get('sb','--')}"
                )
                if r:
                    rs = r.get('stats', {})
                    lines.append(
                        f"       vs DROP {r['name']} — "
                        f"AVG {rs.get('avg','--')} OBP {rs.get('obp','--')} SLG {rs.get('slg','--')} "
                        f"(OPS +{u.get('ops_delta',0):.3f})"
                    )

    cats = results.get('categories', {})
    if cats and 'error' not in cats:
        cat_list = cats.get('cats', [])
        winning  = [c['category'] for c in cat_list if c['winning']]
        losing   = [c['category'] for c in cat_list if not c['winning']]
        lines.append(
            f"\nH2H CATEGORIES (Week {cats.get('week','?')}): "
            f"{cats.get('my_team','Me')} vs {cats.get('opp','Opp')} — "
            f"leading {cats.get('leading',0)}/{len(cat_list)}"
        )
        if winning:
            lines.append(f"  Winning: {', '.join(winning)}")
        if losing:
            lines.append(f"  Losing:  {', '.join(losing)}")

    news = results.get('news', {})
    transactions = news.get('transactions', [])
    if transactions:
        lines.append('\nRECENT MLB TRANSACTIONS:')
        for t in transactions:
            lines.append(f"  {t['date']}  {t['type']}: {t['player']} ({t['team']})")

    roster_schedule = news.get('roster_schedule', [])
    if roster_schedule:
        lines.append('\nYOUR ROSTER SCHEDULE (next 7 days):')
        for p in roster_schedule:
            lines.append(f"  {p['name']} ({p['team']}) — {p['games']} games")

    density = news.get('schedule_density', {})
    if density:
        items  = list(density.items())
        top    = items[:5]
        bottom = items[-5:]
        lines.append('\nFULL SCHEDULE DENSITY (next 7 days):')
        lines.append('  Most games:  ' + ', '.join(f"{t} ({n})" for t, n in top))
        lines.append('  Least games: ' + ', '.join(f"{t} ({n})" for t, n in bottom))

    lines.append(
        '\nGive me 2-3 prioritized, specific moves to make today. '
        'Be direct — no filler.'
    )
    return '\n'.join(lines)


def _header(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def _print_injuries(alerts):
    _header("INJURY ALERTS")
    if not alerts:
        print("  No issues found.")
        return
    for a in alerts:
        status = a.get('status', '')
        print(f"  {a['player_name']} (ID {a['player_id']}) — {status}")
        print(f"    Slot: {a['current_slot']}  |  Action: {a['action']}")


def _print_streaming(data):
    _header(f"STREAMING TARGETS")
    if isinstance(data, dict) and 'error' in data:
        print(f"  Error: {data['error']}")
        return

    targets = data.get('targets', [])
    drops   = data.get('drop_candidates', [])

    if not targets:
        print("  No streamers found with upcoming starts.")
    else:
        print(f"  {'Name':<22} {'Src':<4} {'Date':<12} {'Opp':<6} {'H/A':<4} {'ERA':<6} {'WHIP':<6} {'K/9':<5} {'IP':<6} {'OppOPS'}")
        print(f"  {'-'*88}")
        for p in targets:
            s       = p.get('stats', {})
            era     = f"{s.get('era',  '--')}"
            whip    = f"{s.get('whip', '--')}"
            k9      = f"{s.get('k9',   '--')}"
            ip      = f"{s.get('ip',   '--')}"
            ha      = 'Home' if p.get('home') else 'Away'
            opp_ops = f"{p['opp_ops']:.3f}" if p.get('opp_ops') else '--'
            extra   = f" +{len(p['extra_starts'])} more" if p.get('extra_starts') else ''
            print(f"  {p['player_name']:<22} {p['source']:<4} {p['start_date']:<12} "
                  f"{p['opponent'][:5]:<6} {ha:<4} {era:<6} {whip:<6} {k9:<5} {ip:<6} {opp_ops}{extra}")

    if drops:
        print(f"\n  Drop candidates (no start in window):")
        for d in drops:
            print(f"    {d['player_name']} (ID {d['player_id']}, slot {d['slot']})")


def _print_waivers(data):
    _header("WAIVER / FA BATTER TARGETS")
    if not data:
        print("  No clear upgrade opportunities found.")
        return
    for pos, upgrades in data.items():
        print(f"\n  [{pos}]")
        for u in upgrades:
            c  = u['candidate']
            cs = c.get('stats', {})
            r  = u.get('replaces')
            rs = r.get('stats', {}) if r else {}
            delta = u.get('ops_delta', 0)

            src = 'W' if c.get('source') not in ('FA', '') else 'FA'
            print(f"    ADD  {c['name']} ({src}, {c.get('percent_owned', 0)}% owned) — "
                  f"AVG {cs.get('avg','--')} OBP {cs.get('obp','--')} SLG {cs.get('slg','--')} "
                  f"HR {cs.get('hr','--')} SB {cs.get('sb','--')}")
            if r:
                print(f"    DROP {r['name']} — "
                      f"AVG {rs.get('avg','--')} OBP {rs.get('obp','--')} SLG {rs.get('slg','--')} "
                      f"HR {rs.get('hr','--')} SB {rs.get('sb','--')}  "
                      f"(OPS +{delta:.3f})")
            else:
                print(f"    (fills empty {pos} slot)")


def _print_waiver_pitchers(data):
    _header("WAIVER / FA RELIEVER TARGETS")
    if not data:
        print("  No RP targets found.")
        return
    print(f"  {'Name':<22} {'Src':<4} {'%Own':<6} {'SV':<4} {'HLD':<4} {'ERA':<6} {'WHIP':<6} {'K/9':<5} {'IP'}")
    print(f"  {'-'*70}")
    for p in data:
        s = p['stats']
        print(f"  {p['name']:<22} {p['source']:<4} {p['percent_owned']:<6} "
              f"{s.get('saves',0):<4} {s.get('holds',0):<4} "
              f"{s.get('era','--'):<6} {s.get('whip','--'):<6} "
              f"{s.get('k9','--'):<5} {s.get('ip','--')}")


def _print_categories(data):
    _header("H2H CATEGORY SNAPSHOT")
    if 'error' in data:
        print(f"  Could not parse matchup data: {data['error']}")
        return

    week    = data.get('week', '?')
    my_team = data.get('my_team', 'Your team')
    opp     = data.get('opp', 'Opponent')
    cats    = data.get('cats', [])
    leading = data.get('leading', 0)
    total   = len(cats)

    print(f"  Week {week}: {my_team} vs {opp}")
    print(f"  Leading {leading} of {total} categories\n")
    print(f"  {'Cat':<6} {'Yours':>8} {'Theirs':>8}  Result")
    print(f"  {'-'*35}")
    for c in cats:
        result = 'WIN' if c['winning'] else 'lose'
        print(f"  {c['category']:<6} {str(c['mine']):>8} {str(c['theirs']):>8}  {result}")


def _print_news(data):
    _header("MLB NEWS & SCHEDULE")

    transactions = data.get('transactions', [])
    if transactions:
        print("  Recent transactions (last 3 days):")
        for t in transactions:
            print(f"    {t['date']}  {t['type']:<40} {t['player']} ({t['team']})")
    else:
        print("  No notable transactions in the last 3 days.")

    roster_schedule = data.get('roster_schedule', [])
    if roster_schedule:
        print("\n  Your active players — games this week:")
        for p in roster_schedule:
            print(f"    {p['name']:<25} {p['team']:<25} {p['games']} games")

    density = data.get('schedule_density', {})
    if density:
        items  = list(density.items())
        top    = items[:5]
        bottom = items[-5:]
        print("\n  All teams — schedule density:")
        print(f"    Most:  " + "  ".join(f"{t} ({n})" for t, n in top))
        print(f"    Least: " + "  ".join(f"{t} ({n})" for t, n in bottom))


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def date_arg(s):
    return datetime.date.fromisoformat(s)


def main():
    parser = argparse.ArgumentParser(prog='fantasy_bot', description='Yahoo Fantasy Baseball CLI')
    parser.add_argument('--cache', action='store_true',
                        help='Cache Yahoo API responses in .yahoo_cache.pkl (dev/debug)')
    sub    = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('roster', help='Show your roster')
    p.add_argument('--date', type=date_arg, default=datetime.date.today(), metavar='YYYY-MM-DD')
    p.set_defaults(func=cmd_roster)

    p = sub.add_parser('lineup', help='Set player positions')
    p.add_argument('--date', type=date_arg, default=datetime.date.today(), metavar='YYYY-MM-DD')
    p.add_argument('--set', metavar='ID:POS', action='append', required=True,
                   help='player_id:position, repeatable')
    p.set_defaults(func=cmd_lineup)

    p = sub.add_parser('free-agents', help='List free agents by position')
    p.add_argument('position', help='e.g. SP, RP, 2B, OF, P, B')
    p.set_defaults(func=cmd_free_agents)

    p = sub.add_parser('waivers', help='List players on waivers')
    p.set_defaults(func=cmd_waivers)

    p = sub.add_parser('add', help='Add a free agent')
    p.add_argument('player_id', type=int)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser('drop', help='Drop a player')
    p.add_argument('player_id', type=int)
    p.set_defaults(func=cmd_drop)

    p = sub.add_parser('add-drop', help='Add a free agent and drop a player atomically')
    p.add_argument('add_id', type=int, metavar='ADD_ID')
    p.add_argument('drop_id', type=int, metavar='DROP_ID')
    p.set_defaults(func=cmd_add_drop)

    p = sub.add_parser('claim', help='Submit a waiver claim (add + drop)')
    p.add_argument('add_id', type=int, metavar='ADD_ID')
    p.add_argument('drop_id', type=int, metavar='DROP_ID')
    p.set_defaults(func=cmd_claim)

    p = sub.add_parser('clear-cache', help='Delete the Yahoo API response cache')
    p.set_defaults(func=cmd_clear_cache)

    p = sub.add_parser('advise', help='AI-powered recommendations based on full analysis')
    p.set_defaults(func=cmd_advise)

    p = sub.add_parser('analyze', help='Roster analysis, streaming targets, waiver pickups')
    p.add_argument('--section', choices=['injuries', 'streaming', 'waivers', 'waiver_pitchers', 'categories', 'news'],
                   default=None, help='Run one section only (default: all)')
    p.add_argument('--days', type=int, default=3,
                   help='Days ahead to look for streaming starts (default: 3)')
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser('email-report', help='Email daily report (analyze + advise)')
    p.add_argument('--email', type=str, default=None,
                   help='Recipient email (default: EMAIL_TO env var)')
    p.set_defaults(func=cmd_email_report)

    args = parser.parse_args()
    if args.cache:
        from mlb_stats import enable_cache
        enable_cache()
    args.func(args)


if __name__ == '__main__':
    main()
