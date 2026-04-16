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

def _send_email(subject, html_body, to_addr=None):
    """Send HTML email. Uses Gmail by default."""
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

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = to_addr
    msg.attach(MIMEText(html_body, 'html'))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        print(f"✓ Email sent to {to_addr}")
    except Exception as e:
        print(f"✗ Failed to send email: {e}", file=sys.stderr)
        raise


def _md_to_html(text):
    """Convert basic markdown (**, *, #, newlines) to HTML."""
    import re
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'^#{1,3} (.+)$', r'<strong>\1</strong>', text, flags=re.MULTILINE)
    text = text.replace('\n', '<br>')
    return text


def _h(title, color='#1a1a2e'):
    return (f'<h2 style="margin:24px 0 6px;padding:8px 12px;background:{color};'
            f'color:#fff;font-size:13px;letter-spacing:1px;font-family:monospace">'
            f'{title}</h2>')


def _table(headers, rows, col_styles=None):
    """Render a simple HTML table."""
    th_style = ('padding:5px 10px;text-align:left;background:#2d2d44;'
                'color:#ccc;font-size:11px;font-family:monospace')
    td_style = 'padding:4px 10px;font-size:12px;font-family:monospace;border-bottom:1px solid #eee'
    html = ['<table style="width:100%;border-collapse:collapse;margin-bottom:8px">']
    html.append('<tr>' + ''.join(f'<th style="{th_style}">{h}</th>' for h in headers) + '</tr>')
    for row in rows:
        html.append('<tr>' + ''.join(
            f'<td style="{td_style}{";"+col_styles[i] if col_styles and i < len(col_styles) else ""}">{cell}</td>'
            for i, cell in enumerate(row)
        ) + '</tr>')
    html.append('</table>')
    return '\n'.join(html)


def _badge(text, color='#555', bg='#eee'):
    return (f'<span style="display:inline-block;padding:1px 6px;border-radius:3px;'
            f'background:{bg};color:{color};font-size:11px;font-family:monospace">{text}</span>')


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
    if 'recent_form' in results:
        _print_recent_form(results['recent_form'])
    if 'two_start_pitchers' in results:
        _print_two_start_pitchers(results['two_start_pitchers'])
    if 'category_targets' in results:
        _print_category_targets(results['category_targets'])
    if 'trade_candidates' in results:
        _print_trade_candidates(results['trade_candidates'])


def cmd_advise(args):
    import os

    lg      = get_league(cache=args.cache)
    results = analyze(lg, progress=_progress)
    print()

    _header("AI ADVISOR")
    prompt = _build_advise_prompt(results)

    if getattr(args, 'print_prompt', False):
        print(prompt)
        return

    if os.environ.get('ANTHROPIC_API_KEY'):
        _advise_claude(prompt)
    elif os.environ.get('GOOGLE_API_KEY'):
        _advise_gemini(prompt)
    else:
        print("  Set ANTHROPIC_API_KEY or GOOGLE_API_KEY to enable AI advice.")


def cmd_email_report(args):
    """Run analyze + advise and email the report as HTML."""
    to_addr = args.email if hasattr(args, 'email') and args.email else None

    lg = get_league(cache=args.cache)
    results = analyze(lg, progress=None)

    # Pull all sections
    injuries     = results.get('injuries', [])
    streaming    = results.get('streaming', {})
    waiver_p     = results.get('waiver_pitchers', [])
    two_start    = results.get('two_start_pitchers', [])
    cat_targets  = results.get('category_targets', {}) if isinstance(results.get('category_targets'), dict) else {}
    trade        = results.get('trade_candidates', {}) if isinstance(results.get('trade_candidates'), dict) else {}
    recent_form  = results.get('recent_form', {}) if isinstance(results.get('recent_form'), dict) else {}
    news         = results.get('news', {}) if isinstance(results.get('news'), dict) else {}
    cats         = results.get('categories', {}) if isinstance(results.get('categories'), dict) else {}

    # Build subject with category lead if available
    cat_list = cats.get('cats', [])
    leading  = cats.get('leading', 0)
    tied     = cats.get('tied', 0)
    opp      = cats.get('opp', '')
    if cat_list and opp:
        total    = len(cat_list)
        trailing = total - leading - tied
        tied_str = f', Tied {tied}' if tied else ''
        score    = f'{leading} - {trailing}'
        outcome  = 'Winning' if leading > trailing else ('Losing' if trailing > leading else 'Tied')
        subject = (f"Fantasy Baseball — {datetime.date.today().strftime('%a, %b %d')} "
                   f"| Leading {leading}{tied_str} of {total} · {outcome} {score} vs {opp}")
    else:
        subject = f"Fantasy Baseball — {datetime.date.today().strftime('%a, %b %d')}"

    # Get AI advice first (goes at top of email)
    prompt = _build_advise_prompt(results)
    try:
        if os.environ.get('ANTHROPIC_API_KEY'):
            advice_text = _get_advise_text_claude(prompt)
        elif os.environ.get('GOOGLE_API_KEY'):
            advice_text = _get_advise_text_gemini(prompt)
        else:
            advice_text = 'Set ANTHROPIC_API_KEY or GOOGLE_API_KEY to enable AI advice.'
    except Exception as e:
        advice_text = f'Error generating advice: {e}'

    # Wrapper style
    wrap  = 'max-width:680px;margin:0 auto;font-family:monospace;color:#222;background:#fff'
    muted = 'color:#888;font-size:11px'
    none_msg = '<p style="color:#999;font-size:12px;margin:4px 12px">None.</p>'

    html = [f'<div style="{wrap}">']

    # ── Header ──────────────────────────────────────────────────────────────
    html.append(
        f'<div style="background:#1a1a2e;padding:16px 20px">'
        f'<span style="color:#fff;font-size:16px;font-weight:bold">⚾ Fantasy Baseball</span>'
        f'<span style="float:right;color:#aaa;font-size:12px">'
        f'{datetime.date.today().strftime("%A, %B %d, %Y")}</span></div>'
    )
    if cat_list and opp:
        my_team = cats.get('my_team', 'Your team')
        total_c  = len(cat_list)
        trailing = total_c - leading - tied
        tied_str = f', Tied {tied}' if tied else ''
        score    = f'{leading} - {trailing}'
        outcome  = 'Winning' if leading > trailing else ('Losing' if trailing > leading else 'Tied')
        win_color = '#2ecc71' if leading > trailing else '#e74c3c'
        html.append(
            f'<div style="background:#2d2d44;padding:8px 20px;color:#ccc;font-size:12px">'
            f'Week {cats.get("week","?")}  ·  {my_team} vs {opp}  ·  '
            f'<span style="color:{win_color};font-weight:bold">'
            f'Leading {leading}{tied_str} of {total_c} categories  ·  {outcome} {score}'
            f'</span>'
            f'</div>'
        )

    # ── AI Advisor (top) ────────────────────────────────────────────────────
    html.append(_h('AI ADVISOR RECOMMENDATIONS', '#2c3e50'))
    html.append(
        f'<div style="padding:10px 16px;background:#f9f9f9;border-left:3px solid #2c3e50;'
        f'font-size:13px;line-height:1.6">{_md_to_html(advice_text)}</div>'
    )

    # ── Injuries ────────────────────────────────────────────────────────────
    html.append(_h('INJURY ALERTS', '#c0392b'))
    if injuries:
        rows = []
        for a in injuries:
            action_color = '#c0392b' if 'immediately' in a['action'] else '#e67e22'
            rows.append([
                f'<strong>{a["player_name"]}</strong>',
                _badge(a['status'], '#fff', '#c0392b'),
                a['current_slot'],
                f'<span style="color:{action_color}">{a["action"]}</span>',
            ])
        html.append(_table(['Player', 'Status', 'Slot', 'Action'], rows))
    else:
        html.append('<p style="color:#2ecc71;font-size:12px;margin:4px 12px">All clear.</p>')

    # ── Streaming Targets ───────────────────────────────────────────────────
    html.append(_h('STREAMING TARGETS (next 3 days)'))
    targets = streaming.get('targets', []) if isinstance(streaming, dict) else []
    if targets:
        rows = []
        for t in targets[:5]:
            s  = t.get('stats', {})
            ha = _badge('Home', '#fff', '#27ae60') if t.get('home') else _badge('Away', '#555', '#ddd')
            rows.append([
                f'<strong>{t["player_name"]}</strong>',
                _badge(t['source'], '#fff', '#555'),
                t['start_date'],
                t['opponent'],
                ha,
                f'{s.get("era", "--")}',
                f'{s.get("whip", "--")}',
                f'{s.get("k9", "--")}',
            ])
        html.append(_table(['Name', 'Src', 'Date', 'Opp', 'H/A', 'ERA', 'WHIP', 'K/9'], rows))
    else:
        html.append(none_msg)

    # ── Waiver RP Targets ───────────────────────────────────────────────────
    html.append(_h('WAIVER / FA RELIEVER TARGETS'))
    if waiver_p:
        rows = []
        for p in waiver_p[:5]:
            s = p['stats']
            rows.append([
                f'<strong>{p["name"]}</strong>',
                _badge(p['source'], '#fff', '#555'),
                f'{p["percent_owned"]}%',
                f'<strong>{p["sv_hld"]}</strong>',
                s.get('era', '--'),
                s.get('whip', '--'),
            ])
        html.append(_table(['Name', 'Src', '%Own', 'SV+HLD', 'ERA', 'WHIP'], rows))
    else:
        html.append(none_msg)

    # ── Two-Start Pitchers ──────────────────────────────────────────────────
    html.append(_h('TWO-START PITCHERS (this week)'))
    if two_start:
        rows = []
        for p in two_start:
            starts_html = ''
            for s in p['starts']:
                ha      = _badge('Home', '#fff', '#27ae60') if s['home'] else _badge('Away', '#555', '#ddd')
                opp_ops = f'  <span style="{muted}">OppOPS {s["opp_ops"]:.3f}</span>' if s.get('opp_ops') else ''
                proj    = f'  <span style="{muted}">(proj)</span>' if s.get('projected') else ''
                starts_html += f'{s["date"]} vs {s["opponent"]} {ha}{opp_ops}{proj}<br>'
            slot = p.get('slot', '')
            if slot == 'FA':
                src_badge = _badge('FA', '#fff', '#e67e22')
            elif slot == 'W':
                src_badge = _badge('W', '#fff', '#8e44ad')
            else:
                src_badge = ''
            rows.append([f'<strong>{p["player_name"]}</strong> {src_badge}', starts_html.rstrip('<br>')])
        html.append(_table(['Pitcher', 'Starts'], rows))
    else:
        html.append(none_msg)

    # ── Category Targets ────────────────────────────────────────────────────
    html.append(_h('CATEGORY TARGETS'))
    chase   = cat_targets.get('chase', [])
    concede = cat_targets.get('concede', [])
    if chase or concede:
        if chase:
            html.append('<p style="margin:6px 12px 2px;font-size:11px;color:#27ae60"><strong>CHASE</strong></p>')
            rows = []
            for c in chase:
                sug = f'<br><span style="{muted}">{c["suggestion"]}</span>' if c.get('suggestion') else ''
                rows.append([
                    f'<strong>{c["category"]}</strong>',
                    str(c['mine']),
                    str(c['theirs']),
                    f'{c["gap_pct"]*100:.1f}% behind',
                    sug or '—',
                ])
            html.append(_table(['Category', 'Yours', 'Theirs', 'Gap', 'Suggestion'], rows))
        if concede:
            html.append('<p style="margin:6px 12px 2px;font-size:11px;color:#e74c3c"><strong>CONCEDE</strong> '
                        f'<span style="{muted}">(gap too large to close)</span></p>')
            rows = []
            for c in concede:
                rows.append([
                    f'<strong>{c["category"]}</strong>',
                    str(c['mine']),
                    str(c['theirs']),
                    f'{c["gap_pct"]*100:.1f}% behind',
                    c.get('suggestion', '—'),
                ])
            html.append(_table(['Category', 'Yours', 'Theirs', 'Gap', 'Suggestion'], rows))
    else:
        html.append(none_msg)

    # ── Trade Candidates ────────────────────────────────────────────────────
    html.append(_h('TRADE CANDIDATES'))
    sell = trade.get('sell_high', [])
    buy  = trade.get('buy_low', [])
    if sell or buy:
        if sell:
            html.append('<p style="margin:6px 12px 2px;font-size:11px"><strong>SELL HIGH</strong></p>')
            rows = [[f'<strong>{p["player_name"]}</strong>', p['reason']] for p in sell]
            html.append(_table(['Player', 'Reason'], rows))
        if buy:
            html.append('<p style="margin:6px 12px 2px;font-size:11px"><strong>BUY LOW</strong></p>')
            rows = [
                [f'<strong>{p["player_name"]}</strong>',
                 f'{p["season_ops"]:.3f}',
                 f'{p["percent_owned"]}%']
                for p in buy[:5]
            ]
            html.append(_table(['Player', 'Season OPS', '%Own'], rows))
    else:
        html.append(none_msg)

    # ── Recent Form ─────────────────────────────────────────────────────────
    html.append(_h('RECENT FORM (last 14 days)'))
    hot_bat  = recent_form.get('hot_batters', [])
    cold_bat = recent_form.get('cold_batters', [])
    hot_pit  = recent_form.get('hot_pitchers', [])
    cold_pit = recent_form.get('cold_pitchers', [])

    # Check if cold players appear in trade sell-high list
    sell_names = {p['player_name'] for p in sell} if sell else set()

    if any([hot_bat, cold_bat, hot_pit, cold_pit]):
        def _form_rows_bat(players, delta_color):
            rows = []
            for p in players:
                note = _badge('→ Sell?', '#fff', '#e74c3c') if p['player_name'] in sell_names else ''
                rows.append([
                    f'<strong>{p["player_name"]}</strong>',
                    f'{p["season_ops"]:.3f}',
                    f'<span style="color:{delta_color};font-weight:bold">{p["recent_ops"]:.3f}</span>',
                    f'<span style="color:{delta_color}">{p["ops_delta"]:+.3f}</span>',
                    note,
                ])
            return rows

        def _form_rows_pit(players, delta_color):
            rows = []
            for p in players:
                note = _badge('→ Sell?', '#fff', '#e74c3c') if p['player_name'] in sell_names else ''
                rows.append([
                    f'<strong>{p["player_name"]}</strong>',
                    f'{p["season_era"]:.2f}',
                    f'<span style="color:{delta_color};font-weight:bold">{p["recent_era"]:.2f}</span>',
                    f'<span style="color:{delta_color}">{p["era_delta"]:+.2f}</span>',
                    note,
                ])
            return rows

        if hot_bat:
            html.append('<p style="margin:6px 12px 2px;font-size:11px;color:#27ae60"><strong>HOT BATTERS</strong></p>')
            html.append(_table(['Player', 'Season OPS', 'L14 OPS', 'Δ', ''], _form_rows_bat(hot_bat, '#27ae60')))
        if cold_bat:
            html.append('<p style="margin:6px 12px 2px;font-size:11px;color:#e74c3c"><strong>COLD BATTERS</strong></p>')
            html.append(_table(['Player', 'Season OPS', 'L14 OPS', 'Δ', ''], _form_rows_bat(cold_bat, '#e74c3c')))
        if hot_pit:
            html.append('<p style="margin:6px 12px 2px;font-size:11px;color:#27ae60"><strong>HOT PITCHERS</strong></p>')
            html.append(_table(['Player', 'Season ERA', 'L14 ERA', 'Δ', ''], _form_rows_pit(hot_pit, '#27ae60')))
        if cold_pit:
            html.append('<p style="margin:6px 12px 2px;font-size:11px;color:#e74c3c"><strong>COLD PITCHERS</strong></p>')
            html.append(_table(['Player', 'Season ERA', 'L14 ERA', 'Δ', ''], _form_rows_pit(cold_pit, '#e74c3c')))
    else:
        html.append('<p style="color:#999;font-size:12px;margin:4px 12px">No significant form swings.</p>')

    # ── Recent Transactions ──────────────────────────────────────────────────
    html.append(_h('RECENT TRANSACTIONS'))
    transactions = news.get('transactions', [])
    if transactions:
        rows = [
            [t['date'], t['type'], f'<strong>{t["player"]}</strong>', t.get('team', '')]
            for t in transactions[:5]
        ]
        html.append(_table(['Date', 'Type', 'Player', 'Team'], rows))
    else:
        html.append(none_msg)

    # ── Roster Schedule ──────────────────────────────────────────────────────
    html.append(_h('ROSTER SCHEDULE (most games this week)'))
    roster_schedule = news.get('roster_schedule', [])
    if roster_schedule:
        rows = [
            [f'<strong>{p["name"]}</strong>', p['team'],
             _badge(f'{p["games"]} games', '#fff', '#2c3e50')]
            for p in roster_schedule[:8]
        ]
        html.append(_table(['Player', 'Team', 'Games'], rows))
    else:
        html.append(none_msg)

    # ── Footer ───────────────────────────────────────────────────────────────
    html.append(
        f'<div style="margin-top:24px;padding:10px 16px;background:#f0f0f0;'
        f'color:#aaa;font-size:10px;text-align:center">'
        f'Generated {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}</div>'
    )
    html.append('</div>')

    _send_email(subject, '\n'.join(html), to_addr)


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
    import os, time
    client = genai.Client(api_key=os.environ['GOOGLE_API_KEY'])
    full_prompt = 'You are a concise fantasy baseball expert. Give actionable advice only.\n\n' + prompt
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )
    models = ['gemini-2.5-flash', 'gemini-2.5-flash-lite']
    for model in models:
        for attempt in range(3):
            try:
                for chunk in client.models.generate_content_stream(
                    model=model, contents=full_prompt, config=config,
                ):
                    if chunk.text:
                        print(chunk.text, end='', flush=True)
                print()
                return
            except Exception as e:
                if '503' in str(e) or 'UNAVAILABLE' in str(e) or '404' in str(e) or 'NOT_FOUND' in str(e):
                    if attempt < 2:
                        print(f"\n  [{model} unavailable] Retrying in 15s... (attempt {attempt + 2}/3)")
                        time.sleep(15)
                    else:
                        print(f"\n  [{model} unavailable after 3 attempts, trying fallback]")
                        break
                else:
                    raise


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
    import time
    client = genai.Client(api_key=os.environ['GOOGLE_API_KEY'])
    full_prompt = 'You are a concise fantasy baseball expert. Give actionable advice only.\n\n' + prompt
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )
    models = ['gemini-2.5-flash', 'gemini-2.5-flash-lite']
    for model in models:
        for attempt in range(3):
            try:
                chunks = []
                for chunk in client.models.generate_content_stream(
                    model=model, contents=full_prompt, config=config,
                ):
                    if chunk.text:
                        chunks.append(chunk.text)
                return ''.join(chunks)
            except Exception as e:
                if '503' in str(e) or 'UNAVAILABLE' in str(e) or '404' in str(e) or 'NOT_FOUND' in str(e):
                    if attempt < 2:
                        print(f"  [{model} unavailable] Retrying in 15s... (attempt {attempt + 2}/3)")
                        time.sleep(15)
                    else:
                        print(f"  [{model} unavailable after 3 attempts, trying fallback]")
                        break
                else:
                    raise
    raise RuntimeError("All Gemini models unavailable")


def _build_advise_prompt(results):
    lines = []

    # League context header
    today = datetime.date.today()
    day_name = today.strftime('%A')
    cats = results.get('categories', {})
    cat_list = cats.get('cats', []) if cats and 'error' not in cats else []
    scoring_cats = ', '.join(c['category'] for c in cat_list) if cat_list else 'unknown'

    standings = results.get('standings', {})
    if standings and 'error' not in standings:
        rank  = standings.get('rank', '?')
        wins  = standings.get('wins', '?')
        losses = standings.get('losses', '?')
        ties  = standings.get('ties', 0)
        total = standings.get('total_teams', '?')
        record_str = f"{wins}-{losses}" + (f"-{ties}" if ties else '')
        lines.append(
            f"League: {total}-team H2H categories. Scoring: {scoring_cats}.\n"
            f"Standings: {record_str}, rank {rank}/{total}. Today: {day_name}."
        )
    else:
        lines.append(
            f"League: H2H categories. Scoring: {scoring_cats}.\n"
            f"Today: {day_name}."
        )

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

    if cat_list:
        lines.append(
            f"\nH2H CATEGORIES (Week {cats.get('week','?')}): "
            f"{cats.get('my_team','Me')} vs {cats.get('opp','Opp')} — "
            f"leading {cats.get('leading',0)}/{len(cat_list)}"
        )
        for c in cat_list:
            result = 'WIN' if c['winning'] else ('TIE' if c.get('tied') else 'LOSE')
            lines.append(f"  {c['category']:<6} {str(c.get('mine','?')):>8} vs {str(c.get('theirs','?')):<8}  {result}")

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

    recent_form = results.get('recent_form', {})
    if isinstance(recent_form, dict):
        hot_bat  = recent_form.get('hot_batters', [])
        cold_bat = recent_form.get('cold_batters', [])
        hot_pit  = recent_form.get('hot_pitchers', [])
        cold_pit = recent_form.get('cold_pitchers', [])
        if hot_bat or cold_bat or hot_pit or cold_pit:
            lines.append('\nRECENT FORM (last 14 days):')
        if hot_bat:
            lines.append('  Hot batters:')
            for p in hot_bat:
                lines.append(
                    f"    {p['player_name']} — season OPS {p['season_ops']:.3f} "
                    f"→ last-14d OPS {p['recent_ops']:.3f} ({p['ops_delta']:+.3f})"
                )
        if cold_bat:
            lines.append('  Cold batters:')
            for p in cold_bat:
                lines.append(
                    f"    {p['player_name']} — season OPS {p['season_ops']:.3f} "
                    f"→ last-14d OPS {p['recent_ops']:.3f} ({p['ops_delta']:+.3f})"
                )
        if hot_pit:
            lines.append('  Hot pitchers:')
            for p in hot_pit:
                lines.append(
                    f"    {p['player_name']} — season ERA {p['season_era']:.2f} "
                    f"→ last-14d ERA {p['recent_era']:.2f} ({p['era_delta']:+.2f})"
                )
        if cold_pit:
            lines.append('  Cold pitchers:')
            for p in cold_pit:
                lines.append(
                    f"    {p['player_name']} — season ERA {p['season_era']:.2f} "
                    f"→ last-14d ERA {p['recent_era']:.2f} ({p['era_delta']:+.2f})"
                )

    two_start = results.get('two_start_pitchers', [])
    if two_start:
        lines.append('\nTWO-START PITCHERS (this week — roster + available streamers):')
        for p in two_start:
            opps = ', '.join(
                f"vs {s['opponent']} ({'home' if s['home'] else 'away'})"
                + (f" OPS {s['opp_ops']:.3f}" if s.get('opp_ops') else '')
                + (' (proj)' if s.get('projected') else '')
                for s in p['starts']
            )
            lines.append(f"  {p['player_name']} — {len(p['starts'])} starts: {opps}")

    cat_targets = results.get('category_targets', {})
    if isinstance(cat_targets, dict):
        chase   = cat_targets.get('chase', [])
        concede = cat_targets.get('concede', [])
        if chase or concede:
            lines.append('\nCATEGORY TARGETS:')
        if chase:
            lines.append('  Chase (catchable):')
            for c in chase:
                sug = f" — {c['suggestion']}" if c.get('suggestion') else ''
                lines.append(
                    f"    {c['category']}: {c['mine']} vs {c['theirs']} "
                    f"({c['gap_pct']*100:.1f}% behind){sug}"
                )
        if concede:
            lines.append('  Concede (too far behind):')
            for c in concede:
                lines.append(
                    f"    {c['category']}: {c['mine']} vs {c['theirs']} "
                    f"({c['gap_pct']*100:.1f}% behind)"
                )

    trade = results.get('trade_candidates', {})
    if isinstance(trade, dict):
        sell = trade.get('sell_high', [])
        buy  = trade.get('buy_low', [])
        if sell or buy:
            lines.append('\nTRADE CANDIDATES:')
        if sell:
            lines.append('  Sell high (likely to regress):')
            for p in sell:
                lines.append(f"    {p['player_name']} — {p['reason']}")
        if buy:
            lines.append('  Buy low (strong season stats, on waivers):')
            for p in buy[:3]:
                lines.append(f"    {p['player_name']} — season OPS {p['season_ops']:.3f}")

    lines.append(
        '\nGive me 2-3 specific moves, prioritized by: (1) impact on this week\'s matchup, '
        'then (2) roster improvement. One sentence per move. No preamble. '
        'For every add, specify who to drop.'
    )
    return '\n'.join(lines)


def _print_category_targets(data):
    _header("CATEGORY TARGETS")
    chase   = data.get('chase', [])
    protect = data.get('protect', [])
    concede = data.get('concede', [])

    if not chase and not protect and not concede:
        print("  No matchup data available.")
        return

    if chase:
        print("  CHASE (catchable — prioritize these categories):")
        for c in chase:
            gap = f"{c['gap_pct']*100:.1f}% behind"
            sug = f"  → {c['suggestion']}" if c.get('suggestion') else ''
            print(f"    {c['category']:<6} {c['mine']:>8}  vs  {c['theirs']:>8}  ({gap}){sug}")

    if concede:
        print("\n  CONCEDE (too far behind — don't burn resources):")
        for c in concede:
            gap = f"{c['gap_pct']*100:.1f}% behind"
            print(f"    {c['category']:<6} {c['mine']:>8}  vs  {c['theirs']:>8}  ({gap})")

    if protect:
        print("\n  PROTECT (currently winning — hold steady):")
        cats = ', '.join(c['category'] for c in protect)
        print(f"    {cats}")


def _print_trade_candidates(data):
    _header("TRADE CANDIDATES")
    sell_high = data.get('sell_high', [])
    buy_low   = data.get('buy_low', [])

    if not sell_high and not buy_low:
        print("  No trade candidates identified.")
        return

    if sell_high:
        print("  SELL HIGH (extreme recent form — sell before regression):")
        for p in sell_high:
            print(f"    {p['player_name']:<25} {p['reason']}")

    if buy_low:
        print("\n  BUY LOW (strong season stats, available on waivers):")
        for p in buy_low[:5]:
            print(f"    {p['player_name']:<25} season OPS {p['season_ops']:.3f}  "
                  f"({p['percent_owned']}% owned)")


def _print_recent_form(data):
    _header("RECENT FORM (last 14 days)")
    hot_bat  = data.get('hot_batters', [])
    cold_bat = data.get('cold_batters', [])
    hot_pit  = data.get('hot_pitchers', [])
    cold_pit = data.get('cold_pitchers', [])

    if not any([hot_bat, cold_bat, hot_pit, cold_pit]):
        print("  No significant form swings on your active roster.")
        return

    if hot_bat:
        print("  HOT BATTERS (OPS delta >= +0.100):")
        print(f"  {'Name':<22} {'Slot':<6} {'SeasonOPS':<10} {'14dOPS':<9} {'Delta':<8} {'AVG':<6} {'HR':<4} {'SB':<4} PA")
        print(f"  {'-'*82}")
        for p in hot_bat:
            print(f"  {p['player_name']:<22} {p['slot']:<6} {p['season_ops']:<10.3f} "
                  f"{p['recent_ops']:<9.3f} +{p['ops_delta']:<7.3f} "
                  f"{p['recent_avg']:<6} {p['recent_hr']:<4} {p['recent_sb']:<4} {p['recent_pa']}")

    if cold_bat:
        print("\n  COLD BATTERS (OPS delta <= -0.100):")
        print(f"  {'Name':<22} {'Slot':<6} {'SeasonOPS':<10} {'14dOPS':<9} {'Delta':<8} {'AVG':<6} {'HR':<4} {'SB':<4} PA")
        print(f"  {'-'*82}")
        for p in cold_bat:
            print(f"  {p['player_name']:<22} {p['slot']:<6} {p['season_ops']:<10.3f} "
                  f"{p['recent_ops']:<9.3f} {p['ops_delta']:<8.3f} "
                  f"{p['recent_avg']:<6} {p['recent_hr']:<4} {p['recent_sb']:<4} {p['recent_pa']}")

    if hot_pit:
        print("\n  HOT PITCHERS (ERA last 14d significantly better):")
        print(f"  {'Name':<22} {'Slot':<6} {'SeasonERA':<10} {'14dERA':<8} {'Delta':<8} {'WHIP':<6} {'K/9':<6} IP")
        print(f"  {'-'*75}")
        for p in hot_pit:
            print(f"  {p['player_name']:<22} {p['slot']:<6} {p['season_era']:<10.2f} "
                  f"{p['recent_era']:<8.2f} {p['era_delta']:<8.2f} "
                  f"{p['recent_whip']:<6} {p['recent_k9']:<6.1f} {p['recent_ip']}")

    if cold_pit:
        print("\n  COLD PITCHERS (ERA last 14d significantly worse):")
        print(f"  {'Name':<22} {'Slot':<6} {'SeasonERA':<10} {'14dERA':<8} {'Delta':<8} {'WHIP':<6} {'K/9':<6} IP")
        print(f"  {'-'*75}")
        for p in cold_pit:
            print(f"  {p['player_name']:<22} {p['slot']:<6} {p['season_era']:<10.2f} "
                  f"{p['recent_era']:<8.2f} +{p['era_delta']:<7.2f} "
                  f"{p['recent_whip']:<6} {p['recent_k9']:<6.1f} {p['recent_ip']}")


def _print_two_start_pitchers(data):
    _header("TWO-START PITCHERS (this week)")
    if not data:
        print("  No pitchers with 2+ starts this week.")
        return
    for p in data:
        starts = p['starts']
        print(f"  {p['player_name']:<25} ({p['slot']:<4}) — {len(starts)} starts:")
        for s in starts:
            ha      = 'home' if s['home'] else 'away'
            opp_ops = f"  OppOPS {s['opp_ops']:.3f}" if s.get('opp_ops') else ''
            proj    = '  (proj)' if s.get('projected') else ''
            print(f"    {s['date']}  vs {s['opponent']:<25} ({ha}){opp_ops}{proj}")


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
    tied    = data.get('tied', 0)
    total   = len(cats)

    trailing = total - leading - tied
    tied_str = f', Tied {tied}' if tied else ''
    score    = f'{leading} - {trailing}'
    outcome  = 'Winning' if leading > trailing else ('Losing' if trailing > leading else 'Tied')
    print(f"  Week {week}: {my_team} vs {opp}")
    print(f"  Leading {leading}{tied_str} of {total} categories  ·  {outcome} {score}\n")
    print(f"  {'Cat':<6} {'Yours':>8} {'Theirs':>8}  Result")
    print(f"  {'-'*35}")
    for c in cats:
        result = 'WIN' if c['winning'] else ('tie' if c.get('tied') else 'lose')
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
    p.add_argument('--print-prompt', action='store_true', help='Print the AI prompt and exit without calling any model')
    p.set_defaults(func=cmd_advise)

    p = sub.add_parser('analyze', help='Roster analysis, streaming targets, waiver pickups')
    p.add_argument('--section',
                   choices=['injuries', 'streaming', 'waivers', 'waiver_pitchers',
                            'categories', 'news', 'recent_form', 'two_start_pitchers',
                            'category_targets', 'trade_candidates', 'standings'],
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
