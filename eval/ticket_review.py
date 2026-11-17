"""Human review of the labeled tickets: each label next to the shop facts and the full text of the cited rules.

    python -m eval.ticket_review make                          # writes eval/tickets/review.html (gitignored)
    python -m eval.ticket_review apply <review.json> --by <name>

Open the page (.claude/launch.json "ticket-review", or just open the file), mark each ticket OK or Problem
(+ note), then "Download review.json" (or copy it). `apply` records OKs in tickets.json.

The facts on each card are computed from data/shop.json and data/policies, never copied from the label's
reasoning, so the reviewer checks the label against the data rather than against the labeler's explanation.

Staleness is per ticket: an OK stores `verified_hash` = hash(ticket label + email + shop + policies). If the
label, the email, the shop data or any policy page changes afterwards, the hash no longer matches, the test
suite fails, and that ticket needs a new review. Stdlib only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from data.generate_shop import OUT as SHOP_JSON
from data.policies import POLICY_DIR, load_rules

TICKETS = Path(__file__).parent / "tickets" / "tickets.json"
PAGE_OUT = TICKETS.with_name("review.html")
VERIFY_FIELDS = ("verified_by", "verified_at", "verified_hash")
ORDER_ID = re.compile(r"\bORD-\d+\b")


# ---------------------------------------------------------------- hashing

def data_hash() -> str:
    h = hashlib.sha256(SHOP_JSON.read_bytes())
    for p in sorted(POLICY_DIR.glob("*.md")):
        h.update(p.name.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def ticket_hash(ticket: dict, data: str) -> str:
    """What a reviewer approved: the ticket minus its verification fields, plus the data it was checked on."""
    content = {k: v for k, v in ticket.items() if k not in VERIFY_FIELDS}
    payload = json.dumps(content, sort_keys=True, ensure_ascii=False) + data
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def load() -> dict:
    return json.loads(TICKETS.read_text(encoding="utf-8"))


def save(doc: dict) -> None:
    TICKETS.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- facts (from the data, not the label)

def money(cents: int | None) -> str:
    if cents is None:
        return "—"
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100}.{cents % 100:02d}"


class Shop:
    def __init__(self) -> None:
        shop = json.loads(SHOP_JSON.read_text(encoding="utf-8"))
        self.today = dt.date.fromisoformat(shop["shop_today"])
        self.customers = {c["id"]: c for c in shop["customers"]}
        self.by_email = {c["email"]: c for c in shop["customers"]}
        self.orders = {o["id"]: o for o in shop["orders"]}
        self.refunds: dict[str, list[dict]] = defaultdict(list)
        for r in shop["refunds"]:
            self.refunds[r["order_id"]].append(r)
        self.orders_of: dict[str, list[dict]] = defaultdict(list)
        for o in shop["orders"]:
            self.orders_of[o["customer_id"]].append(o)

    def ago(self, iso: str | None) -> str:
        if not iso:
            return "—"
        return f"{iso} ({(self.today - dt.date.fromisoformat(iso)).days} days ago)"

    def left_to_refund(self, order: dict) -> int:
        return order["total_paid_cents"] - sum(r["amount_cents"] for r in self.refunds[order["id"]])


def mentioned_orders(ticket: dict) -> list[str]:
    seen: list[str] = []
    for oid in ORDER_ID.findall(ticket["subject"] + "\n" + ticket["body"]):
        if oid not in seen:
            seen.append(oid)
    label = ticket["expected"]["order_id"]
    if label and label not in seen:
        seen.append(label)
    return seen


def e(s: object) -> str:
    return html.escape(str(s))


def sender_html(shop: Shop, ticket: dict) -> str:
    c = shop.by_email.get(ticket["from"])
    if not c:
        return f'<p class="warn">Sender <code>{e(ticket["from"])}</code> is NOT a customer.</p>'
    tier = '<span class="tag vip">VIP</span>' if c["tier"] == "vip" else "standard"
    return (f'<p>Sender: <b>{e(c["name"])}</b> <code>{c["id"]}</code> · {tier} · '
            f'{len(shop.orders_of[c["id"]])} order(s)</p>')


def order_html(shop: Shop, oid: str, sender: dict | None) -> str:
    o = shop.orders.get(oid)
    if not o:
        return f'<div class="order"><p class="warn"><code>{e(oid)}</code>: no such order.</p></div>'
    if sender and o["customer_id"] == sender["id"]:
        owner = '<span class="ok">belongs to the sender</span>'
    else:
        other = shop.customers[o["customer_id"]]
        owner = f'<span class="warn">belongs to {other["id"]} ({e(other["email"])}), NOT the sender</span>'
    rows = "".join(
        f'<tr><td><code>{i["sku"]}</code></td><td>{e(i["name"])}'
        f'{" <span class=tag>final sale</span>" if i["final_sale"] else ""}</td><td>{i["category"]}</td>'
        f'<td class="n">{i["qty"]}</td><td class="n">{money(i["unit_price_cents"])}</td>'
        f'<td class="n">{money(i["qty"] * i["unit_price_cents"])}</td></tr>'
        for i in o["items"]
    )
    refunds = "".join(
        f'<li>{r["id"]}: {money(r["amount_cents"])} ({r["reason"]}) on {r["created_at"]}</li>'
        for r in shop.refunds[oid]
    ) or "<li>none</li>"
    return f"""<div class="order">
  <p><b><code>{oid}</code></b> · {owner} · status <b>{o["status"]}</b></p>
  <p class="dates">placed {shop.ago(o["placed_at"])} · shipped {shop.ago(o["shipped_at"])} ·
     delivered {shop.ago(o["delivered_at"])}</p>
  <table><tr><th>SKU</th><th>Item</th><th>Category</th><th>Qty</th><th>Unit</th><th>Line</th></tr>{rows}
  <tr><td colspan=5>subtotal</td><td class="n">{money(o["subtotal_cents"])}</td></tr>
  <tr><td colspan=5>shipping</td><td class="n">{money(o["shipping_cents"])}</td></tr>
  <tr><td colspan=5><b>paid</b></td><td class="n"><b>{money(o["total_paid_cents"])}</b></td></tr></table>
  <p>Past refunds:</p><ul>{refunds}</ul>
  <p class="muted">Refund ceiling (REF-3: paid − already refunded, shipping included):
     {money(shop.left_to_refund(o))}. This is the upper limit, not the expected refund; whether shipping is
     refunded is decided by REF-2.</p>
</div>"""


def sender_orders_html(shop: Shop, sender: dict | None) -> str:
    """Compact list of the sender's orders: lets the reviewer judge 'which order did they mean?'."""
    if not sender:
        return ""
    rows = "".join(
        f'<li><code>{o["id"]}</code> {o["status"]}, delivered {shop.ago(o["delivered_at"])}: '
        f'{e(", ".join(i["name"] for i in o["items"]))}</li>'
        for o in shop.orders_of[sender["id"]]
    )
    return f"<details><summary>All of the sender's orders</summary><ul>{rows}</ul></details>"


def card(shop: Shop, rules: dict, ticket: dict, current_hash: str) -> str:
    exp = ticket["expected"]
    sender = shop.by_email.get(ticket["from"])
    oids = mentioned_orders(ticket)
    orders = "".join(order_html(shop, oid, sender) for oid in oids) or '<p class="warn">No order id in the email.</p>'
    show_all = not oids or exp["order_id"] is None
    rule_blocks = "".join(
        f'<div class="rule"><b>{rid}</b> {e(rules[rid].title)} <span class="muted">({rules[rid].page})</span>'
        f'<blockquote>{e(rules[rid].text)}</blockquote></div>'
        if rid in rules else f'<p class="warn">Unknown rule id {e(rid)}</p>'
        for rid in exp["rule_ids"]
    )
    label = (f'<b>{exp["action"]}</b> · order <code>{e(exp["order_id"] or "none")}</code> · '
             f'refund amount <b>{money(exp["amount_cents"])}</b>')
    verified = ""
    if ticket.get("verified_hash"):
        state = "current" if ticket["verified_hash"] == current_hash else "STALE (changed since review)"
        verified = f'<span class="muted">verified by {e(ticket["verified_by"])} on {ticket["verified_at"]}, {state}</span>'
    inj = '<span class="tag inj">injection</span>' if ticket["injection"] else ""
    return f"""
<section class="q" id="{ticket['id']}" data-id="{ticket['id']}" data-hash="{current_hash}">
  <header><span class="id">{ticket['id']}</span><span class="tag">{e(ticket['scenario'])}</span>{inj}{verified}</header>
  <h4>Email (untrusted, as received on {ticket['received_at']})</h4>
  <div class="email"><div class="meta">From: <code>{e(ticket['from'])}</code><br>Subject: {e(ticket['subject'])}</div>
  <pre>{e(ticket['body'])}</pre></div>
  <h4>Label</h4><p class="label">{label}</p>
  <h4>Shop facts (computed from shop.json)</h4>
  {sender_html(shop, ticket)}{orders}{sender_orders_html(shop, sender) if show_all else ""}
  <h4>Cited rules (full text)</h4>{rule_blocks}
  <h4>Labeler's reasoning (check it against the facts above)</h4><p class="reasoning">{e(ticket['reasoning'])}</p>
  <div class="verdict">
    <label><input type="radio" name="v-{ticket['id']}" value="ok"> OK</label>
    <label><input type="radio" name="v-{ticket['id']}" value="problem"> Problem</label>
    <input type="text" class="note" placeholder="what's wrong? (optional for OK)">
  </div>
</section>"""


# ---------------------------------------------------------------- page

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ticket Label Review</title>
<style>
:root {{ --bg:#fafaf9; --card:#fff; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4; --mark:#fde68a;
        --ok:#15803d; --bad:#b91c1c; --code:#f5f5f4; --vip:#7c3aed; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#1c1917; --card:#292524; --fg:#f5f5f4; --muted:#a8a29e;
        --line:#44403c; --mark:#854d0e; --code:#1c1917; --ok:#4ade80; --bad:#f87171; --vip:#c4b5fd; }} }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; }}
main {{ max-width:900px; margin:0 auto; padding:16px; }}
.bar {{ position:sticky; top:0; background:var(--bg); padding:10px 0; border-bottom:1px solid var(--line);
       display:flex; gap:12px; align-items:center; flex-wrap:wrap; z-index:1; }}
button {{ font:inherit; padding:6px 14px; border-radius:6px; border:1px solid var(--line); background:var(--card);
         color:var(--fg); cursor:pointer; }}
.q {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin:16px 0;
      scroll-margin-top:64px; }}
.q.done-ok {{ border-left:5px solid var(--ok); }} .q.done-problem {{ border-left:5px solid var(--bad); }}
header {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; color:var(--muted); font-size:13px; }}
.id {{ font-weight:700; color:var(--fg); }}
.tag {{ padding:1px 8px; border-radius:99px; border:1px solid var(--line); font-size:12px; }}
.tag.inj {{ border-color:var(--bad); color:var(--bad); }} .tag.vip {{ border-color:var(--vip); color:var(--vip); }}
h4 {{ margin:14px 0 4px; font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
.email {{ border:1px solid var(--line); border-radius:6px; padding:8px; }}
.meta {{ font-size:13px; color:var(--muted); }}
pre {{ white-space:pre-wrap; word-break:break-word; background:var(--code); padding:8px; border-radius:6px;
      font:13px/1.45 ui-monospace, monospace; margin:6px 0 0; }}
.label {{ font-size:16px; background:var(--mark); display:inline-block; padding:2px 8px; border-radius:4px; }}
.order {{ border-left:3px solid var(--line); padding-left:10px; margin:8px 0; }}
.order p {{ margin:4px 0; }} .dates {{ font-size:13px; }}
table {{ border-collapse:collapse; font-size:13px; margin:4px 0; width:100%; }}
th, td {{ border-bottom:1px solid var(--line); padding:3px 6px; text-align:left; }}
td.n {{ text-align:right; font-variant-numeric:tabular-nums; }}
ul {{ margin:2px 0; padding-left:20px; font-size:13px; }}
.ok {{ color:var(--ok); font-weight:600; }} .warn {{ color:var(--bad); font-weight:600; }}
.muted {{ color:var(--muted); }}
blockquote {{ margin:4px 0 8px; padding:6px 10px; border-left:3px solid var(--mark); background:var(--code); }}
.reasoning {{ font-style:italic; color:var(--muted); }}
code {{ background:var(--code); padding:0 4px; border-radius:4px; }}
.verdict {{ display:flex; gap:14px; align-items:center; flex-wrap:wrap; border-top:1px solid var(--line);
           padding-top:10px; margin-top:10px; }}
.note {{ flex:1; min-width:200px; font:inherit; padding:5px 8px; border-radius:6px; border:1px solid var(--line);
        background:var(--bg); color:var(--fg); }}
</style></head><body><main>
<h1>Ticket label review</h1>
<p>{count} labeled tickets. Shop "today" is <b>{today}</b>; every ticket was received that day. For each one: given
the email, the shop facts and the rule text, is the <b>action</b> right, is the <b>order</b> right, is the
<b>amount</b> exactly right, and are the cited rules the ones that decide it? Your marks stay in this browser
until you download them.</p>
<div class="bar"><span id="progress"></span>
  <button id="download">Download review.json</button>
  <button id="copy">Copy review to clipboard</button>
  <span id="copied"></span></div>
<details id="rawbox"><summary>Show review JSON (select all, copy, paste into chat)</summary>
  <textarea id="raw" readonly rows="10" style="width:100%;font:12px ui-monospace,monospace"></textarea>
</details>
{cards}
</main>
<script>
const KEY = "ticket-review-{data_hash}";
let state = {{}};
try {{ state = JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch (e) {{}}
const save = () => {{ try {{ localStorage.setItem(KEY, JSON.stringify(state)); }} catch (e) {{}} render(); }};
function render() {{
  const qs = document.querySelectorAll(".q");
  let done = 0, bad = 0;
  qs.forEach(q => {{
    const s = state[q.dataset.id];
    q.classList.remove("done-ok", "done-problem");
    if (s && s.verdict && s.hash === q.dataset.hash) {{
      done++; q.classList.add("done-" + s.verdict); if (s.verdict === "problem") bad++;
    }}
  }});
  document.getElementById("progress").textContent = `${{done}} / ${{qs.length}} reviewed, ${{bad}} problem(s)`;
}}
document.querySelectorAll(".q").forEach(q => {{
  const id = q.dataset.id, hash = q.dataset.hash;
  if (state[id] && state[id].hash !== hash) delete state[id];  // label changed since this mark: review again
  const s = state[id] || {{}};
  const note = q.querySelector(".note");
  note.value = s.note || "";
  q.querySelectorAll("input[type=radio]").forEach(r => {{
    r.checked = r.value === s.verdict;
    r.addEventListener("change", () => {{ state[id] = {{...state[id], verdict: r.value, hash}}; save(); }});
  }});
  note.addEventListener("input", () => {{ state[id] = {{...state[id], note: note.value, hash}}; save(); }});
}});
const payload = () => JSON.stringify({{data_hash: "{data_hash}", reviews: state}}, null, 2);
const showRaw = () => {{ document.getElementById("raw").value = payload(); }};
document.getElementById("download").addEventListener("click", () => {{
  showRaw();
  try {{
    const blob = new Blob([payload()], {{type: "application/json"}});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "review.json";
    document.body.appendChild(a); a.click(); a.remove();
  }} catch (e) {{ document.getElementById("rawbox").open = true; }}
}});
document.getElementById("copy").addEventListener("click", async () => {{
  showRaw();
  const msg = document.getElementById("copied");
  try {{ await navigator.clipboard.writeText(payload()); msg.textContent = "Copied - paste it into the chat."; }}
  catch (e) {{
    const box = document.getElementById("raw"); document.getElementById("rawbox").open = true;
    box.focus(); box.select();
    try {{ document.execCommand("copy"); msg.textContent = "Copied - paste it into the chat."; }}
    catch (e2) {{ msg.textContent = "Select the text below and copy it (Ctrl+C)."; }}
  }}
}});
document.getElementById("rawbox").addEventListener("toggle", showRaw);
render();
</script></body></html>
"""


def render_page(doc: dict) -> str:
    shop, rules, data = Shop(), load_rules(), data_hash()
    cards = "".join(card(shop, rules, t, ticket_hash(t, data)) for t in doc["tickets"])
    return PAGE.format(count=len(doc["tickets"]), today=shop.today.isoformat(), data_hash=data, cards=cards)


# ---------------------------------------------------------------- apply

def apply_review(doc: dict, review: dict, by: str, today: str) -> list[str]:
    """Record OKs whose hash matches the current ticket; clear verification on problems. Returns a report."""
    data = data_hash()
    marks = review.get("reviews", {})
    ok, problems, stale, missing = 0, [], [], []
    for t in doc["tickets"]:
        m = marks.get(t["id"])
        current = ticket_hash(t, data)
        if t.get("verified_hash") and t["verified_hash"] != current:  # changed since it was verified
            t["verified_by"] = t["verified_at"] = t["verified_hash"] = None
        if not m or not m.get("verdict"):
            missing.append(t["id"])
        elif m.get("hash") != current:
            stale.append(t["id"])
        elif m["verdict"] == "ok":
            t["verified_by"], t["verified_at"], t["verified_hash"] = by, today, current
            ok += 1
        else:
            t["verified_by"] = t["verified_at"] = t["verified_hash"] = None
            problems.append(f"{t['id']}: {m.get('note') or '(no note)'}")
    report = [f"verified {ok}, problems {len(problems)}, stale {len(stale)}, not reviewed {len(missing)}"]
    report += [f"  problem {p}" for p in problems]
    if stale:
        report.append("  stale (changed since you reviewed it, review again): " + ", ".join(stale))
    if missing:
        report.append("  not reviewed: " + ", ".join(missing))
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("make", help="write eval/tickets/review.html")
    a = sub.add_parser("apply", help="record a downloaded review.json into tickets.json")
    a.add_argument("review", type=Path)
    a.add_argument("--by", required=True)
    args = ap.parse_args()

    doc = load()
    if args.cmd == "make":
        PAGE_OUT.write_text(render_page(doc), encoding="utf-8", newline="\n")
        print(f"wrote {PAGE_OUT}")
        return 0
    report = apply_review(doc, json.loads(args.review.read_text(encoding="utf-8")), args.by,
                          dt.date.today().isoformat())
    save(doc)
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
