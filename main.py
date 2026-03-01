import os
import json
import logging
from logging.handlers import RotatingFileHandler
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

import yaml
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from core.news_engine import NewsEngine
from core.kpi_engine import KPIEngine
from core.trade_tracker import TradeTracker
from core.market_engine import MarketEngine
from core.risk_engine import RiskEngine, InstrumentSpec
from core.equity_engine import EquityEngine

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

SUBSCRIBERS_PATH = "data/subscribers.json"
AUTO_JOB_NAME = "auto_news_job"
STARTUP_EXPIRED_ON_BOOT = 0


def load_config(path: str = "config.yml") -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def setup_logging() -> None:
    os.makedirs("logs", exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    if root.handlers:
        root.handlers.clear()

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    fh = RotatingFileHandler(
        "logs/bot.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)


def _load_subscribers() -> Dict[str, Any]:
    os.makedirs(os.path.dirname(SUBSCRIBERS_PATH) or ".", exist_ok=True)
    if not os.path.exists(SUBSCRIBERS_PATH):
        return {"chat_ids": [], "interval_minutes": 5}
    try:
        with open(SUBSCRIBERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        data.setdefault("chat_ids", [])
        data.setdefault("interval_minutes", 5)
        return data
    except Exception:
        return {"chat_ids": [], "interval_minutes": 5}


def _save_subscribers(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(SUBSCRIBERS_PATH) or ".", exist_ok=True)
    with open(SUBSCRIBERS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _add_chat(chat_id: int) -> None:
    data = _load_subscribers()
    ids = set(int(x) for x in data.get("chat_ids", []))
    ids.add(int(chat_id))
    data["chat_ids"] = sorted(ids)
    _save_subscribers(data)


def _remove_chat(chat_id: int) -> None:
    data = _load_subscribers()
    ids = [int(x) for x in data.get("chat_ids", []) if int(x) != int(chat_id)]
    data["chat_ids"] = ids
    _save_subscribers(data)


def _get_interval_minutes() -> int:
    data = _load_subscribers()
    try:
        return max(1, int(data.get("interval_minutes", 5)))
    except Exception:
        return 5


def _set_interval_minutes(minutes: int) -> None:
    data = _load_subscribers()
    data["interval_minutes"] = max(1, int(minutes))
    _save_subscribers(data)


async def send_long_message(
    update: Optional[Update],
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    chat_id: Optional[int] = None,
) -> None:
    MAX = 3800
    parts: List[str] = []
    s = (text or "").strip()

    while len(s) > MAX:
        cut = s.rfind("\n", 0, MAX)
        if cut < 500:
            cut = MAX
        parts.append(s[:cut].strip())
        s = s[cut:].strip()

    if s:
        parts.append(s)

    target_chat = chat_id or (update.effective_chat.id if update and update.effective_chat else None)
    if target_chat is None:
        return

    for p in parts:
        await context.bot.send_message(chat_id=target_chat, text=p)


cfg = load_config()
news_cfg = cfg.get("news") or {}
risk_cfg = cfg.get("risk") or {}
inst_cfg = cfg.get("instruments") or {}
trade_map = cfg.get("trades") or {}
tier2_rules = cfg.get("tier2") or {}

# build instruments
instruments: Dict[str, InstrumentSpec] = {}
for sym, spec in inst_cfg.items():
    instruments[sym] = InstrumentSpec(
        yfinance_symbol=str(spec.get("yfinance_symbol", sym)),
        point_value_inr=float(spec.get("point_value_inr", 0.0)),
        lot_step=float(spec.get("lot_step", 1)),
        lot_min=float(spec.get("lot_min", 1)),
        atr_low_pct=float(spec.get("atr_low_pct", 0.0)),
        atr_high_pct=float(spec.get("atr_high_pct", 999.0)),
        factors=tuple(spec.get("factors") or []),
        tier=int(spec.get("tier", 1)),
    )

market = MarketEngine(cache_seconds=20)
tracker = TradeTracker(path="data/trades.json")
kpi = KPIEngine(path="data/kpi.json")

equity_engine = EquityEngine(
    path="data/equity.json",
    start_equity_inr=float(risk_cfg.get("equity_inr", 0.0)),
)

risk_engine = RiskEngine(
    equity_inr=float(risk_cfg.get("equity_inr", 0.0)),
    risk_pct_per_trade=float(risk_cfg.get("risk_pct_per_trade", 0.01)),
    max_portfolio_risk_pct=float(risk_cfg.get("max_portfolio_risk_pct", 0.03)),
    max_factor_trades=int(risk_cfg.get("max_factor_trades", 1)),
    instruments=instruments,
)

news_engine = NewsEngine(
    feeds=(news_cfg.get("feeds") or {}),
    category_rules=(news_cfg.get("categories") or {}),
    trade_map=trade_map,
    tier2_rules=tier2_rules,
    instruments=instruments,
    max_age_minutes=int((cfg.get("news_provider") or {}).get("max_age_minutes", 240)),
    confirm_window_minutes=int(news_cfg.get("confirm_window_minutes", 30)),
    similarity_threshold=float(news_cfg.get("similarity_threshold", 0.28)),
    upgrade_extend_ttl_minutes=int(news_cfg.get("upgrade_extend_ttl_minutes", 240)),
    max_open_per_category=int(news_cfg.get("max_open_per_category", 3)),
    market=market,
    tracker=tracker,
    kpi=kpi,
    risk=risk_engine,
    equity=equity_engine,
)
news_engine.set_timezone((cfg.get("mvp") or {}).get("timezone", "UTC"))


def _read_kpi_state() -> Tuple[int, int, int, float]:
    path = getattr(kpi, "path", "data/kpi.json")
    try:
        if not os.path.exists(path):
            return (0, 0, 0, 0.0)
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        total = int(d.get("total_closed", 0))
        tp = int(d.get("hits_tp", 0))
        sl = int(d.get("hits_sl", 0))
        acc = float(d.get("accuracy_pct", 0.0))
        acc_r = float(f"{acc:.4f}")
        return (total, tp, sl, acc_r)
    except Exception:
        return (0, 0, 0, 0.0)


def _kpi_changed(before: Tuple[int, int, int, float], after: Tuple[int, int, int, float]) -> bool:
    return before != after


def _startup_trade_state_text(expired_on_boot: int) -> str:
    total = len(getattr(tracker, "_trades", []))
    open_count = len(tracker.list_open())
    path = getattr(tracker, "path", "data/trades.json")
    meta = getattr(tracker, "load_meta", {}) or {}
    last_modified = meta.get("last_modified")
    used_backup = bool(meta.get("used_backup", False))
    load_error = meta.get("load_error")

    try:
        if (not last_modified) and os.path.exists(path):
            last_modified = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).isoformat()
    except Exception:
        last_modified = None

    return (
        "startup_trades_loaded "
        f"total={total} open={open_count} expired_on_boot={expired_on_boot} "
        f"file={path} last_modified={last_modified or 'unknown'} "
        f"used_backup={used_backup} load_error={load_error or 'none'}"
    )


def _log_startup_trade_state(expired_on_boot: int) -> str:
    msg = _startup_trade_state_text(expired_on_boot)
    logging.info(msg)
    return msg


async def startup_integrity_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = _load_subscribers()
    chat_ids = [int(x) for x in data.get("chat_ids", [])]
    if not chat_ids:
        return

    msg = _startup_trade_state_text(expired_on_boot=STARTUP_EXPIRED_ON_BOOT)
    for cid in chat_ids:
        try:
            await context.bot.send_message(chat_id=cid, text=msg)
        except Exception as e:
            logging.warning("startup_integrity_alert_failed chat_id=%s err=%s", cid, e)


def _has_any_actions(r) -> bool:
    created = getattr(r, "created_trades", None) or []
    upgraded = getattr(r, "upgraded_trades", None) or []
    closed = getattr(r, "closed_trades", None) or []
    expired = getattr(r, "expired_trades", None) or []
    return bool(created or upgraded or closed or expired)


def render_actions(r, include_kpi: bool) -> str:
    created = getattr(r, "created_trades", []) or []
    upgraded = getattr(r, "upgraded_trades", []) or []
    closed = getattr(r, "closed_trades", []) or []
    expired = getattr(r, "expired_trades", []) or []

    lines: List[str] = []
    lines.append("⚡ Bot alert: trade actions")

    if created:
        lines.append("")
        lines.append("🟢 Created trades:")
        for t in created[:10]:
            lines.append(
                f"- [{t.category}] {t.direction} {t.symbol} lots={t.lots:g} risk=₹{t.risk_inr:.0f} "
                f"@ {t.entry:.4f} | TP {t.tp:.4f} | SL {t.sl:.4f} | {t.quality}"
            )

    if upgraded:
        lines.append("")
        lines.append("🟦 Upgraded trades:")
        for t in upgraded[:10]:
            lines.append(f"- [{t.category}] {t.id} {t.symbol} -> {t.quality} | sources={len(t.evidence_sources)} score={t.evidence_score_total}")

    if closed:
        lines.append("")
        lines.append("✅ Closed trades:")
        for t in closed[:10]:
            lines.append(
                f"- [{t.category}] {t.id} {t.symbol} outcome={t.outcome} pnl=₹{t.pnl_inr:.0f} @ {t.close_price}"
            )

    if expired:
        lines.append("")
        lines.append("⏳ Expired trades:")
        for t in expired[:15]:
            lines.append(f"- [{t.category}] {t.id} {t.symbol} -> {t.status} ({t.close_reason})")

    if include_kpi:
        lines.append("")
        lines.append("📊 KPI updated:")
        try:
            lines.append(kpi.summary())
        except Exception:
            lines.append("(KPI unavailable)")

    # Equity summary: only meaningful after closes, but safe to show whenever action happens
    if closed:
        lines.append("")
        lines.append(equity_engine.summary())

    return "\n".join(lines)


async def auto_news_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        before_kpi = _read_kpi_state()
        r = news_engine.run_once()
        after_kpi = _read_kpi_state()
        include_kpi = _kpi_changed(before_kpi, after_kpi)

        if not _has_any_actions(r) and not include_kpi:
            return

        msg = render_actions(r, include_kpi=include_kpi)

        data = _load_subscribers()
        chat_ids = [int(x) for x in data.get("chat_ids", [])]
        if not chat_ids:
            return

        for cid in chat_ids:
            await send_long_message(update=None, context=context, text=msg, chat_id=cid)

    except Exception as e:
        logging.exception("auto_news_job failed", exc_info=e)


def ensure_auto_job(app: Application) -> None:
    data = _load_subscribers()
    if not data.get("chat_ids"):
        return

    interval = _get_interval_minutes()

    for j in app.job_queue.get_jobs_by_name(AUTO_JOB_NAME):
        j.schedule_removal()

    app.job_queue.run_repeating(
        auto_news_job,
        interval=interval * 60,
        first=5,
        name=AUTO_JOB_NAME,
    )
    logging.info("Auto job started interval=%s min chats=%s", interval, len(data["chat_ids"]))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled error", exc_info=context.error)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Bot online.\n\n"
        "Commands:\n"
        "/news         -> run once (full output)\n"
        "/trades       -> show open trades\n"
        "/kpi          -> KPI snapshot\n"
        "/autostart 5  -> auto scan every 5 min (alerts only on actions)\n"
        "/autoset 10   -> change interval\n"
        "/autostop     -> stop auto scan\n"
        "/help         -> commands\n"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/news — run pipeline once\n"
        "/trades — list open trades\n"
        "/kpi — KPI snapshot\n"
        "/autostart N — auto scan every N minutes (only when actions happen)\n"
        "/autoset N — change interval\n"
        "/autostop — stop auto scan\n"
    )


def render_news_full(r) -> str:
    stats_line = (
        f"📡 Providers ok={getattr(r, 'providers_ok', 0)} "
        f"ok_empty={getattr(r, 'providers_ok_empty', 0)} "
        f"failed={getattr(r, 'providers_failed', 0)} | "
        f"items={getattr(r, 'items_before_filter', 0)}→{getattr(r, 'items_after_filter', 0)}"
    )

    lines: List[str] = [stats_line, "", "✅ News events (grouped):", ""]
    for ev in getattr(r, "events", [])[:8]:
        for it in ev.items[:6]:
            ts = it.published_at.isoformat() if it.published_at else "time?"
            lines.append(f"[{ev.category}] [{ts[:16].replace('T',' ')}] {it.title}")
            lines.append(f"Publishers: {it.provider}")
            lines.append("")

    lines.append("🗞️ Latest headlines (RAW):")
    lines.append("")
    for it in getattr(r, "event_items", [])[:8]:
        ts = it.published_at.isoformat() if it.published_at else "time?"
        lines.append(f"[{it.category}] [{ts[:16].replace('T',' ')}] {it.title}")
        lines.append(f"• {it.provider}")
        lines.append(f"• {it.url}")
        lines.append("")

    created = getattr(r, "created_trades", []) or []
    upgraded = getattr(r, "upgraded_trades", []) or []
    closed = getattr(r, "closed_trades", []) or []
    expired = getattr(r, "expired_trades", []) or []

    trade_lines: List[str] = []
    if created:
        trade_lines.append("🟢 Created trades:")
        for t in created[:8]:
            trade_lines.append(
                f"- [{t.category}] {t.direction} {t.symbol} lots={t.lots:g} risk=₹{t.risk_inr:.0f} "
                f"@ {t.entry:.4f} | TP {t.tp:.4f} | SL {t.sl:.4f} | {t.quality}"
            )
    if upgraded:
        trade_lines.append("🟦 Upgraded trades:")
        for t in upgraded[:8]:
            trade_lines.append(f"- [{t.category}] {t.id} {t.symbol} -> {t.quality} | sources={len(t.evidence_sources)} score={t.evidence_score_total}")
    if closed:
        trade_lines.append("✅ Closed trades:")
        for t in closed[:8]:
            trade_lines.append(f"- [{t.category}] {t.id} {t.symbol} outcome={t.outcome} pnl=₹{t.pnl_inr:.0f} @ {t.close_price}")
    if expired:
        trade_lines.append("⏳ Expired trades:")
        for t in expired[:12]:
            trade_lines.append(f"- [{t.category}] {t.id} {t.symbol} -> {t.status} ({t.close_reason})")
    if not trade_lines:
        trade_lines.append("No trade actions.")

    if closed:
        trade_lines.append("")
        trade_lines.append(equity_engine.summary())

    return "\n".join(lines + [""] + trade_lines)


async def news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        r = news_engine.run_once()
        msg = render_news_full(r)
        await send_long_message(update, context, msg)
    except Exception as e:
        logging.exception("Error in /news", exc_info=e)
        await update.message.reply_text("❌ /news failed. Check logs/bot.log for the exact error.")


async def trades_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        open_trades = tracker.list_open()
        if not open_trades:
            await update.message.reply_text("No OPEN trades.")
            return

        lines = ["📌 OPEN trades (showing up to 15):"]
        for t in open_trades[:15]:
            lines.append(
                f"- [{t.category}] {t.direction} {t.symbol} lots={t.lots:g} risk=₹{t.risk_inr:.0f} "
                f"@ {t.entry:.4f} | TP {t.tp:.4f} | SL {t.sl:.4f} | {t.quality}"
            )
        await send_long_message(update, context, "\n".join(lines))
    except Exception as e:
        logging.exception("Error in /trades", exc_info=e)
        await update.message.reply_text("❌ /trades failed. Check logs/bot.log.")


async def kpi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text(kpi.summary() + "\n\n" + equity_engine.summary())
    except Exception as e:
        logging.exception("Error in /kpi", exc_info=e)
        await update.message.reply_text("❌ /kpi failed. Check logs/bot.log.")


async def autostart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    _add_chat(chat_id)

    minutes = _get_interval_minutes()
    if context.args:
        try:
            minutes = max(1, int(context.args[0]))
            _set_interval_minutes(minutes)
        except Exception:
            pass

    for j in context.application.job_queue.get_jobs_by_name(AUTO_JOB_NAME):
        j.schedule_removal()

    context.application.job_queue.run_repeating(
        auto_news_job,
        interval=minutes * 60,
        first=3,
        name=AUTO_JOB_NAME,
    )

    await update.message.reply_text(
        f"✅ Auto scan ON. Interval = {minutes} minutes.\n"
        f"Alerts only on: created/upgraded/closed/expired.\n"
        f"KPI is auto-sent only when KPI state changes."
    )


async def autoset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /autoset 10")
        return

    try:
        minutes = max(1, int(context.args[0]))
    except Exception:
        await update.message.reply_text("Usage: /autoset 10 (N must be a number)")
        return

    _set_interval_minutes(minutes)

    jobs = context.application.job_queue.get_jobs_by_name(AUTO_JOB_NAME)
    if jobs:
        for j in jobs:
            j.schedule_removal()
        context.application.job_queue.run_repeating(
            auto_news_job,
            interval=minutes * 60,
            first=3,
            name=AUTO_JOB_NAME,
        )

    await update.message.reply_text(f"✅ Auto scan interval set to {minutes} minutes.")


async def autostop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    _remove_chat(chat_id)

    data = _load_subscribers()
    if not data.get("chat_ids"):
        for j in context.application.job_queue.get_jobs_by_name(AUTO_JOB_NAME):
            j.schedule_removal()
        await update.message.reply_text("🛑 Auto scan OFF. No subscribers remain; job stopped.")
        return

    await update.message.reply_text("🛑 Auto scan OFF for this chat. Others may still be subscribed.")


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Unknown command. Use /help.")


def main():
    global STARTUP_EXPIRED_ON_BOOT
    setup_logging()

    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")

    STARTUP_EXPIRED_ON_BOOT = len(tracker.expire_due())
    startup_msg = _log_startup_trade_state(expired_on_boot=STARTUP_EXPIRED_ON_BOOT)
    if STARTUP_EXPIRED_ON_BOOT:
        logging.info("startup_expire_due_applied count=%s", STARTUP_EXPIRED_ON_BOOT)

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("news", news_cmd))
    app.add_handler(CommandHandler("trades", trades_cmd))
    app.add_handler(CommandHandler("kpi", kpi_cmd))

    app.add_handler(CommandHandler("autostart", autostart_cmd))
    app.add_handler(CommandHandler("autoset", autoset_cmd))
    app.add_handler(CommandHandler("autostop", autostop_cmd))

    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    app.add_error_handler(on_error)

    ensure_auto_job(app)
    app.job_queue.run_once(startup_integrity_job, when=1, name="startup_integrity_job")
    logging.info("startup_integrity_alert_scheduled message=%s", startup_msg)

    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
