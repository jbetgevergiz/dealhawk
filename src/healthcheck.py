import logging
from datetime import datetime, timezone
from flask import Flask, jsonify

logger = logging.getLogger(__name__)

_start_time = datetime.now(timezone.utc)
_app_state = {
    "conn": None,
    "config": None,
    "scheduler_module": None,
}

app = Flask(__name__)

@app.route("/health")
def health():
    from . import scheduler as sched_mod
    from .db import get_all_components, get_consecutive_failures
    from .analysis import check_freshness

    conn = _app_state.get("conn")
    config = _app_state.get("config")

    uptime_seconds = (datetime.now(timezone.utc) - _start_time).total_seconds()
    uptime_hours = round(uptime_seconds / 3600, 3)

    last_run = getattr(sched_mod, "last_scrape_run", None)

    if conn is None or config is None:
        return jsonify({
            "status": "degraded",
            "uptime_hours": uptime_hours,
            "last_scrape_run": last_run,
            "components_tracked": 0,
            "stale_components": 0,
            "consecutive_failures": {}
        }), 503

    try:
        components = get_all_components(conn)
        failure_threshold = config["alerts"]["failure_alert_threshold"]
        staleness_ttl = config["freshness"]["staleness_ttl_hours"]

        stale_count = 0
        failures = {}
        is_degraded = False

        for comp in components:
            cid = comp["id"]
            cname = comp["name"]
            cf = get_consecutive_failures(conn, cid)
            if cf > 0:
                failures[cname] = cf
            if cf >= failure_threshold:
                is_degraded = True

            freshness = check_freshness(conn, cid, staleness_ttl)
            if freshness["status"] in ("stale", "no_data"):
                stale_count += 1

        status = "degraded" if is_degraded else "healthy"
        http_code = 503 if is_degraded else 200

        resp = {
            "status": status,
            "uptime_hours": uptime_hours,
            "last_scrape_run": last_run,
            "components_tracked": len(components),
            "stale_components": stale_count,
            "consecutive_failures": failures
        }
        return jsonify(resp), http_code

    except Exception as e:
        logger.error(f"Healthcheck error: {e}")
        return jsonify({
            "status": "degraded",
            "error": str(e),
            "uptime_hours": uptime_hours,
            "last_scrape_run": last_run,
            "components_tracked": 0,
            "stale_components": 0,
            "consecutive_failures": {}
        }), 503


def start_healthcheck(conn, config, host="0.0.0.0", port=8085):
    _app_state["conn"] = conn
    _app_state["config"] = config
    app.run(host=host, port=port, debug=False, use_reloader=False)
