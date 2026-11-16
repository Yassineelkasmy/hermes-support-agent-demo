#!/usr/bin/env python3
"""Brightleaf support tools: a dependency-free MCP server (JSON-RPC over stdio).

Exposes the back-office actions the support agent is allowed to take.
All data is fictional and lives in ./data.
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path

DATA = Path(__file__).parent / "data"
RETURN_WINDOW_DAYS = 30
STOPWORDS = set("a an the is are was my i to of for in on and or it do does how can what when with not be you me your".split())


def today():
    return dt.date.today()


def load_orders():
    orders = json.loads((DATA / "orders.json").read_text())
    for order in orders.values():  # dates are stored relative to today so the demo never goes stale
        for key in ("placed", "delivered", "eta"):
            offset = order.pop(key + "_day_offset", None)
            if offset is not None:
                order[key] = (today() + dt.timedelta(days=offset)).isoformat()
    return orders


def find_order(order_id, email):
    order = load_orders().get(order_id.strip().upper())
    if not order:
        return None, {"found": False, "message": f"No order with number {order_id}. Format is BL-12345."}
    if order["email"].lower() != email.strip().lower():
        return None, {"found": True, "verified": False,
                      "message": "The email does not match this order. Do not share any order details."}
    return order, None


def words(text):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS and len(w) > 1]


def search_knowledge_base(query):
    terms = set(words(query))
    hits = []
    for path in sorted((DATA / "kb").glob("*.md")):
        lines = path.read_text().splitlines()
        title = lines[0].lstrip("# ").strip()
        for line in lines[1:]:
            if not line.strip():
                continue
            line_words = words(line + " " + title)
            score = sum(1 for t in terms if any(w.startswith(t[:5]) for w in line_words))
            if score:
                hits.append((score, title, line.strip()))
    hits.sort(key=lambda h: -h[0])
    if not hits:
        return {"results": [], "message": "Nothing in the knowledge base matches. Do not guess; offer to escalate."}
    return {"results": [{"article": title, "text": text} for _, title, text in hits[:6]]}


def get_order(order_id, email):
    order, err = find_order(order_id, email)
    if err:
        return err
    return {"found": True, "verified": True, "order_id": order_id.upper(),
            **{k: v for k, v in order.items() if k != "email"}}


def check_return_eligibility(order_id, email):
    order, err = find_order(order_id, email)
    if err:
        return err
    if order["status"] == "processing":
        return {"eligible": False, "reason": "Order has not shipped yet; it can be cancelled free of charge instead."}
    if order["status"] != "delivered":
        return {"eligible": False, "reason": f"Order is '{order['status']}', not delivered yet. A return can start after delivery."}
    days = (today() - dt.date.fromisoformat(order["delivered"])).days
    left = RETURN_WINDOW_DAYS - days
    if left < 0:
        return {"eligible": False, "days_since_delivery": days,
                "reason": f"The {RETURN_WINDOW_DAYS}-day return window closed {-left} day(s) ago. Warranty (2 years) still applies to defects."}
    return {"eligible": True, "days_since_delivery": days, "days_left_in_window": left,
            "note": "Free return label if defective; otherwise 4.90 EUR is deducted from the refund."}


def _save_ticket(ticket):
    path = DATA / "tickets.json"
    tickets = json.loads(path.read_text())
    ticket = {"ticket_id": f"T-{4100 + len(tickets) + 1}", "created": dt.datetime.now().isoformat(timespec="seconds"), **ticket}
    tickets.append(ticket)
    path.write_text(json.dumps(tickets, indent=2))
    return ticket


def create_ticket(category, summary, customer_email, order_id="", priority="normal"):
    ticket = _save_ticket({"type": "ticket", "category": category, "priority": priority, "order_id": order_id.upper(),
                           "customer_email": customer_email, "summary": summary, "status": "open"})
    return {"ticket_id": ticket["ticket_id"], "status": "open",
            "message": "Ticket created. The customer gets an email confirmation; first response within 1 business day."}


def escalate_to_human(reason, conversation_summary, customer_email=""):
    ticket = _save_ticket({"type": "handoff", "priority": "high", "reason": reason, "customer_email": customer_email,
                           "summary": conversation_summary, "status": "waiting_for_agent"})
    hour, weekday = dt.datetime.now().hour, dt.datetime.now().weekday()
    live = weekday < 5 and 9 <= hour < 18
    return {"ticket_id": ticket["ticket_id"], "handed_off": True,
            "expected_wait": "about 5 minutes" if live else "next business day from 09:00 CET (human team is offline now)"}


def s(desc):
    return {"type": "string", "description": desc}


TOOLS = {
    "search_knowledge_base": (search_knowledge_base,
        "Search Brightleaf's official help articles (shipping, returns, warranty, troubleshooting, devices, billing). "
        "Use this before answering any policy or product question.",
        {"query": s("What the customer is asking, in a few keywords")}, ["query"]),
    "get_order": (get_order,
        "Look up an order's status, items and tracking. Requires the order number AND the email used at checkout to verify the customer.",
        {"order_id": s("Order number, e.g. BL-10482"), "email": s("Email address used for the order")}, ["order_id", "email"]),
    "check_return_eligibility": (check_return_eligibility,
        "Check whether an order is still inside the 30-day return window.",
        {"order_id": s("Order number"), "email": s("Email address used for the order")}, ["order_id", "email"]),
    "create_ticket": (create_ticket,
        "Open a support ticket for the back-office team: a return, a warranty claim, an order change or a delivery investigation.",
        {"category": {"type": "string", "enum": ["return", "warranty", "delivery", "order_change", "billing", "other"]},
         "summary": s("What the customer needs, with all relevant facts"), "customer_email": s("Customer email"),
         "order_id": s("Order number if there is one"), "priority": {"type": "string", "enum": ["low", "normal", "high"]}},
        ["category", "summary", "customer_email"]),
    "escalate_to_human": (escalate_to_human,
        "Hand the conversation to a human agent. Use when the customer asks for a person, is upset, or the request is outside policy or the knowledge base.",
        {"reason": s("Why a human is needed"), "conversation_summary": s("Summary so the human does not have to re-ask anything"),
         "customer_email": s("Customer email if known")}, ["reason", "conversation_summary"]),
}


def handle(msg):
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                "serverInfo": {"name": "brightleaf-support", "version": "1.0.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": name, "description": desc,
                           "inputSchema": {"type": "object", "properties": props, "required": req}}
                          for name, (_, desc, props, req) in TOOLS.items()]}
    if method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            raise ValueError(f"Unknown tool: {name}")
        try:
            result, is_error = TOOLS[name][0](**(params.get("arguments") or {})), False
        except Exception as exc:  # report tool failures to the model instead of crashing the server
            result, is_error = {"error": str(exc)}, True
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": is_error}
    raise ValueError(f"Method not found: {method}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if "id" not in msg:  # notification
            continue
        try:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}
        except Exception as exc:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": str(exc)}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
