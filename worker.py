import os
import json
import time
import threading
import logging
import re
import signal
import sys

from pathlib import Path
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv

from flask import Flask, jsonify, send_file, Response, request

from playwright.sync_api import sync_playwright


# ============================================================
# VERSION
# ============================================================

VERSION = "2026-09-29-QR-DIAG-01"


# ============================================================
# KONFIGURATION
# ============================================================

load_dotenv()


SUPABASE_DB_URL = os.getenv(
    "SUPABASE_DB_URL",
    "",
)


DB_SCHEMA = os.getenv(
    "DB_SCHEMA",
    "public",
)


DB_TABLE = os.getenv(
    "DB_TABLE",
    "whatsapp_auftraege",
)


BROWSER_DIR = os.getenv(
    "BROWSER_DIR",
    "whatsapp_browser",
)


DRY_RUN = (
    os.getenv(
        "DRY_RUN",
        "true",
    ).lower()
    == "true"
)


ALLOW_REAL_SEND = (
    os.getenv(
        "ALLOW_REAL_SEND",
        "false",
    ).lower()
    == "true"
)


VERIFY_TIMEOUT_SECONDS = int(
    os.getenv(
        "VERIFY_TIMEOUT_SECONDS",
        "15",
    )
)


VERIFY_POLL_SECONDS = float(
    os.getenv(
        "VERIFY_POLL_SECONDS",
        "0.5",
    )
)


VERIFY_TIME_TOLERANCE_SECONDS = int(
    os.getenv(
        "VERIFY_TIME_TOLERANCE_SECONDS",
        "90",
    )
)


CONTACT_DELAY_SECONDS = float(
    os.getenv(
        "CONTACT_DELAY_SECONDS",
        "1.0",
    )
)


KEEP_BROWSER_OPEN = (
    os.getenv(
        "KEEP_BROWSER_OPEN",
        "true",
    ).lower()
    == "true"
)


HEADLESS = (
    os.getenv(
        "HEADLESS",
        "true",
    ).lower()
    == "true"
)


QR_WEB_PORT = int(
    os.getenv(
        "QR_WEB_PORT",
        "8080",
    )
)


LOGIN_TIMEOUT_SECONDS = int(
    os.getenv(
        "LOGIN_TIMEOUT_SECONDS",
        "0",
    )
)


QR_ACCESS_TOKEN = os.getenv(
    "QR_ACCESS_TOKEN",
    "",
)


DB_RECONNECT_SECONDS = int(
    os.getenv(
        "DB_RECONNECT_SECONDS",
        "10",
    )
)


WORKER_IDLE_SECONDS = int(
    os.getenv(
        "WORKER_IDLE_SECONDS",
        "5",
    )
)


BROWSER_RESTART_DELAY_SECONDS = int(
    os.getenv(
        "BROWSER_RESTART_DELAY_SECONDS",
        "5",
    )
)


HEARTBEAT_SECONDS = int(
    os.getenv(
        "HEARTBEAT_SECONDS",
        "30",
    )
)


QR_SCREENSHOT = (
    "/tmp/whatsapp_login.png"
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


# ============================================================
# GLOBALE ZUSTÄNDE
# ============================================================

app = Flask(__name__)


login_status = "starting"


last_screenshot_time = 0


browser_page = None


worker_started_at = time.time()


last_heartbeat_time = time.time()


shutdown_requested = False


browser_restart_requested = False


db_connected = False


current_order_id = None


current_contact_index = None


# ============================================================
# SIGNAL HANDLER
# ============================================================

def handle_shutdown_signal(
    signum,
    frame,
):
    global shutdown_requested

    logger.info(
        "Shutdown-Signal %s erhalten.",
        signum,
    )

    shutdown_requested = True


signal.signal(
    signal.SIGTERM,
    handle_shutdown_signal,
)


signal.signal(
    signal.SIGINT,
    handle_shutdown_signal,
)


# ============================================================
# FLASK WEBSEITE
# ============================================================

HTML_PAGE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">

    <meta
        name="viewport"
        content="width=device-width, initial-scale=1.0"
    >

    <title>WhatsApp Worker</title>

    <style>
        body {
            font-family: Arial, sans-serif;
            background: #111;
            color: #eee;
            margin: 0;
            padding: 20px;
        }

        h1 {
            margin-top: 0;
        }

        .status {
            padding: 15px;
            background: #222;
            border-radius: 8px;
            margin-bottom: 20px;
        }

        .row {
            margin: 6px 0;
        }

        img {
            max-width: 100%;
            border-radius: 8px;
            border: 1px solid #444;
            display: block;
        }

        .small {
            color: #aaa;
            font-size: 13px;
        }

        code {
            color: #ddd;
        }
    </style>
</head>

<body>

    <h1>WhatsApp Worker</h1>

    <div class="status">

        <div class="row">
            <strong>Status:</strong>
            <span id="status">
                Lade...
            </span>
        </div>

        <div class="row">
            <strong>DB:</strong>
            <span id="db">
                Lade...
            </span>
        </div>

        <div class="row">
            <strong>Browser:</strong>
            <span id="browser">
                Lade...
            </span>
        </div>

        <div class="row">
            <strong>Auftrag:</strong>
            <span id="order">
                -
            </span>
        </div>

        <div class="row">
            <strong>Kontakt:</strong>
            <span id="contact">
                -
            </span>
        </div>

    </div>

    <div>
        <img
            id="qr"
            src="/qr?token=__QR_ACCESS_TOKEN__"
            alt="WhatsApp Screenshot"
        >
    </div>

    <p class="small">
        Der Worker läuft im Hintergrund.
        Status und Screenshot werden automatisch aktualisiert.
    </p>

    <script>

        async function updateStatus() {

            try {

                const response =
                    await fetch("/status");

                const data =
                    await response.json();

                document.getElementById(
                    "status"
                ).textContent =
                    data.status || "-";

                document.getElementById(
                    "db"
                ).textContent =
                    data.database_connected
                    ? "verbunden"
                    : "nicht verbunden";

                document.getElementById(
                    "browser"
                ).textContent =
                    data.browser_available
                    ? "bereit"
                    : "nicht verfügbar";

                document.getElementById(
                    "order"
                ).textContent =
                    data.current_order_id || "-";

                document.getElementById(
                    "contact"
                ).textContent =
                    data.current_contact_index !== null
                    ? data.current_contact_index
                    : "-";

            } catch (error) {

                document.getElementById(
                    "status"
                ).textContent =
                    "Verbindung fehlgeschlagen";
            }
        }


        function updateScreenshot() {

            const image =
                document.getElementById("qr");

            image.src =
                "/qr?token=__QR_ACCESS_TOKEN__&t="
                + new Date().getTime();
        }


        updateStatus();
        updateScreenshot();


        setInterval(
            updateStatus,
            2000
        );


        setInterval(
            updateScreenshot,
            3000
        );

    </script>

</body>
</html>
"""


HTML_PAGE = HTML_PAGE.replace(
    "__QR_ACCESS_TOKEN__",
    QR_ACCESS_TOKEN,
)


@app.route("/")
def index():

    return Response(
        HTML_PAGE,
        mimetype="text/html",
    )


# ============================================================
# QR ENDPOINT
# ============================================================

@app.route("/qr")
def qr():

    if QR_ACCESS_TOKEN:

        token = QR_ACCESS_TOKEN

        if request.args.get(
            "token"
        ) != token:

            logger.warning(
                "Ungültiger QR-Zugriff."
            )

            return Response(
                "Unauthorized",
                status=401,
            )


    if os.path.exists(
        QR_SCREENSHOT
    ):

        try:

            logger.debug(
                "QR-Screenshot ausgeliefert: %s",
                QR_SCREENSHOT,
            )

            return send_file(
                QR_SCREENSHOT,
                mimetype="image/png",
                max_age=0,
                conditional=False,
            )

        except Exception as e:

            logger.exception(
                "QR-Screenshot konnte nicht ausgeliefert werden: %s",
                e,
            )

            return Response(
                "Screenshot konnte nicht ausgeliefert werden.",
                status=500,
            )


    logger.warning(
        "QR-Anfrage: Screenshot existiert noch nicht: %s",
        QR_SCREENSHOT,
    )


    return Response(
        (
            "Noch kein Screenshot verfügbar. "
            f"Pfad={QR_SCREENSHOT}"
        ),
        status=404,
    )


# ============================================================
# STATUS
# ============================================================

@app.route("/status")
def status():

    screenshot_exists = os.path.exists(
        QR_SCREENSHOT
    )


    return jsonify({

        "version":
            VERSION,

        "status":
            login_status,

        "database_connected":
            db_connected,

        "browser_available":
            browser_page is not None,

        "screenshot_available":
            screenshot_exists,

        "screenshot_path":
            QR_SCREENSHOT,

        "last_screenshot":
            last_screenshot_time,

        "current_order_id":
            current_order_id,

        "current_contact_index":
            current_contact_index,

        "browser_restart_requested":
            browser_restart_requested,

        "shutdown_requested":
            shutdown_requested,

        "uptime_seconds":
            int(
                time.time()
                - worker_started_at
            ),
    })


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    healthy = (
        not shutdown_requested
        and login_status
        not in [
            "error",
        ]
    )


    return jsonify({

        "healthy":
            healthy,

        "version":
            VERSION,

        "status":
            login_status,

        "database_connected":
            db_connected,

        "browser_available":
            browser_page is not None,

        "screenshot_available":
            os.path.exists(
                QR_SCREENSHOT
            ),

        "uptime_seconds":
            int(
                time.time()
                - worker_started_at
            ),

    }), (
        200
        if healthy
        else 503
    )


# ============================================================
# WEB SERVER
# ============================================================

def start_webserver():

    logger.info(
        "QR-Webseite gestartet auf Port %s.",
        QR_WEB_PORT,
    )

    logger.info(
        "QR-Webseite erreichbar über /"
    )

    app.run(
        host="0.0.0.0",
        port=QR_WEB_PORT,
        threaded=True,
        use_reloader=False,
    )


# ============================================================
# DATENBANK
# ============================================================

def get_db_connection():

    global db_connected

    if not SUPABASE_DB_URL:

        raise RuntimeError(
            "SUPABASE_DB_URL fehlt."
        )


    conn = psycopg2.connect(
        SUPABASE_DB_URL,
        connect_timeout=10,
    )


    conn.autocommit = False


    db_connected = True


    logger.info(
        "Datenbankverbindung hergestellt."
    )


    return conn


def close_db_connection(
    conn,
):

    global db_connected

    if conn:

        try:

            conn.close()

        except Exception:
            pass


    db_connected = False


def reconnect_db(
    conn,
):

    logger.warning(
        "Versuche Datenbankverbindung neu aufzubauen."
    )


    close_db_connection(
        conn
    )


    while not shutdown_requested:

        try:

            new_conn = get_db_connection()

            logger.info(
                "Datenbankverbindung erfolgreich wiederhergestellt."
            )

            return new_conn

        except Exception as e:

            logger.error(
                "Datenbank-Reconnect fehlgeschlagen: %s",
                e,
            )

            time.sleep(
                DB_RECONNECT_SECONDS
            )


    return None


def ensure_db_connection(
    conn,
):

    if conn is None:

        return get_db_connection()


    try:

        if conn.closed:

            return get_db_connection()


        with conn.cursor() as cur:

            cur.execute(
                "SELECT 1"
            )

            cur.fetchone()


        return conn


    except Exception as e:

        logger.warning(
            "Datenbankverbindung nicht mehr verwendbar: %s",
            e,
        )


        try:

            conn.rollback()

        except Exception:
            pass


        return reconnect_db(
            conn
        )


# ============================================================
# AUFTRAG AKTUALISIEREN
# ============================================================

def update_order(
    conn,
    order_id,
    status=None,
    kontakte=None,
    gesendet=None,
    fehler=None,
):

    fields = []
    values = []


    if status is not None:

        fields.append(
            "status = %s"
        )

        values.append(
            status
        )


    if kontakte is not None:

        fields.append(
            "kontakte = %s"
        )

        values.append(
            json.dumps(
                kontakte,
                ensure_ascii=False,
            )
        )


    if gesendet is not None:

        fields.append(
            "gesendet = %s"
        )

        values.append(
            gesendet
        )


    if fehler is not None:

        fields.append(
            "fehler = %s"
        )

        values.append(
            fehler
        )


    fields.append(
        "aktualisiert_am = NOW()"
    )


    values.append(
        order_id
    )


    sql = f"""
        UPDATE "{DB_SCHEMA}"."{DB_TABLE}"
        SET {", ".join(fields)}
        WHERE id = %s
    """


    with conn.cursor() as cur:

        cur.execute(
            sql,
            values,
        )


    conn.commit()


# ============================================================
# NÄCHSTEN AUFTRAG HOLEN
# ============================================================

def get_next_order(
    conn,
):

    sql = f"""
        SELECT
            id,
            status,
            kontakte,
            gesamt,
            gesendet,
            fehler,
            erstellt_am,
            aktualisiert_am
        FROM "{DB_SCHEMA}"."{DB_TABLE}"
        WHERE status = 'wartet'
        ORDER BY erstellt_am ASC
        LIMIT 1
    """


    with conn.cursor() as cur:

        cur.execute(
            sql
        )

        row = cur.fetchone()


    if not row:

        return None


    (
        order_id,
        status_value,
        kontakte,
        gesamt,
        gesendet,
        fehler,
        erstellt_am,
        aktualisiert_am,
    ) = row


    if isinstance(
        kontakte,
        str,
    ):

        kontakte = json.loads(
            kontakte
        )


    return {

        "id":
            str(order_id),

        "status":
            status_value,

        "kontakte":
            kontakte or [],

        "gesamt":
            gesamt,

        "gesendet":
            gesendet,

        "fehler":
            fehler,

        "erstellt_am":
            erstellt_am,

        "aktualisiert_am":
            aktualisiert_am,
    }


# ============================================================
# SEND-PHASE
# ============================================================

def set_contact_phase(
    conn,
    order,
    kontakte,
    index,
    phase,
):

    kontakt = kontakte[index]


    kontakt[
        "_send_phase"
    ] = phase


    try:

        update_order(
            conn,
            order["id"],
            kontakte=kontakte,
        )


    except Exception as e:

        logger.warning(
            "Send-Phase '%s' konnte nicht gespeichert werden: %s",
            phase,
            e,
        )


        try:

            conn.rollback()

        except Exception:
            pass


# ============================================================
# TELEFONNUMMER
# ============================================================

def normalize_phone(
    phone,
):

    if phone is None:

        return None


    phone = str(
        phone
    ).strip()


    cleaned = ""


    for char in phone:

        if (
            char.isdigit()
            or char == "+"
        ):

            cleaned += char


    if cleaned.startswith("+"):

        cleaned = cleaned[1:]


    if cleaned.startswith("00"):

        cleaned = cleaned[2:]


    if cleaned.startswith("0"):

        cleaned = (
            "49"
            + cleaned[1:]
        )


    return cleaned


# ============================================================
# WHATSAPP POPUPS
# ============================================================

def close_whatsapp_popups(
    page,
):

    closed_any = False


    close_selectors = [

        'button[aria-label="Schließen"]',

        'button[aria-label="Close"]',

        'button[aria-label="Dismiss"]',

        '[role="button"][aria-label="Schließen"]',

        '[role="button"][aria-label="Close"]',

        '[role="button"][aria-label="Dismiss"]',

        'button[title="Schließen"]',

        'button[title="Close"]',

        '[role="button"][title="Schließen"]',

        '[role="button"][title="Close"]',
    ]


    for selector in close_selectors:

        try:

            locator = page.locator(
                selector
            )


            count = locator.count()


            if count == 0:

                continue


            for i in range(
                min(count, 5)
            ):

                try:

                    element = locator.nth(
                        i
                    )


                    if element.is_visible(
                        timeout=500
                    ):

                        element.click(
                            timeout=1000
                        )


                        logger.info(
                            "WhatsApp-Popup geschlossen: %s",
                            selector,
                        )


                        closed_any = True


                        time.sleep(
                            0.5
                        )


                except Exception:
                    pass


        except Exception:
            pass


    xpath_selectors = [

        '//*[self::button or @role="button"]'
        '[normalize-space(@aria-label)="Schließen"]',

        '//*[self::button or @role="button"]'
        '[normalize-space(@aria-label)="Close"]',

        '//*[self::button or @role="button"]'
        '[normalize-space(@title)="Schließen"]',

        '//*[self::button or @role="button"]'
        '[normalize-space(@title)="Close"]',
    ]


    for selector in xpath_selectors:

        try:

            locator = page.locator(
                f"xpath={selector}"
            )


            count = locator.count()


            for i in range(
                min(count, 5)
            ):

                try:

                    element = locator.nth(
                        i
                    )


                    if element.is_visible(
                        timeout=500
                    ):

                        element.click(
                            timeout=1000
                        )


                        closed_any = True


                        time.sleep(
                            0.5
                        )


                except Exception:
                    pass


        except Exception:
            pass


    generic_selectors = [

        '[data-testid="x"]',

        '[data-testid="popup-controls"] button',
    ]


    for selector in generic_selectors:

        try:

            locator = page.locator(
                selector
            )


            count = locator.count()


            for i in range(
                min(count, 3)
            ):

                try:

                    element = locator.nth(
                        i
                    )


                    if not element.is_visible(
                        timeout=300
                    ):

                        continue


                    aria = (
                        element.get_attribute(
                            "aria-label"
                        )
                        or ""
                    )


                    title = (
                        element.get_attribute(
                            "title"
                        )
                        or ""
                    )


                    combined = (
                        aria
                        + " "
                        + title
                    ).lower()


                    if any(
                        word in combined
                        for word in [
                            "schließen",
                            "close",
                            "dismiss",
                        ]
                    ):

                        element.click(
                            timeout=1000
                        )


                        closed_any = True


                        time.sleep(
                            0.5
                        )


                except Exception:
                    pass


        except Exception:
            pass


    return closed_any


# ============================================================
# LOGIN ERKENNEN
# ============================================================

def is_logged_in(
    page,
):

    try:

        close_whatsapp_popups(
            page
        )


        try:

            current_url = page.url


            if (
                "web.whatsapp.com"
                not in current_url
            ):

                return False


        except Exception:
            pass


        positive_selectors = [

            '[data-testid="chat-list"]',

            '[data-testid="side"]',

            '[data-testid="search"]',

            '[aria-label="Suche"]',

            '[aria-label="Search"]',

            '[placeholder="Suche"]',

            '[placeholder="Search"]',

            '[contenteditable="true"][role="textbox"]',
        ]


        positive_count = 0


        for selector in positive_selectors:

            try:

                locator = page.locator(
                    selector
                )


                count = locator.count()


                if count > 0:

                    for i in range(
                        min(count, 3)
                    ):

                        try:

                            if locator.nth(
                                i
                            ).is_visible(
                                timeout=300
                            ):

                                positive_count += 1

                                break


                        except Exception:
                            pass


            except Exception:
                pass


        login_screen_detected = False


        login_texts = [

            "QR-Code",

            "QR code",

            "QR-CODE",

            "Telefonnummer",

            "phone number",

            "Telefonnummer verknüpfen",

            "Link with phone number",
        ]


        try:

            body_text = page.locator(
                "body"
            ).inner_text(
                timeout=1500
            )


            body_lower = body_text.lower()


            for text in login_texts:

                if (
                    text.lower()
                    in body_lower
                ):

                    login_screen_detected = True

                    break


        except Exception:
            pass


        if (
            positive_count >= 1
            and not login_screen_detected
        ):

            return True


        if positive_count >= 2:

            return True


        return False


    except Exception as e:

        logger.debug(
            "Login-Erkennung Fehler: %s",
            e,
        )


        return False


# ============================================================
# SCREENSHOT
# ============================================================

def save_screenshot(page):
    global last_screenshot_time

    try:
        logger.info("SCREENSHOT TEST: URL=%s", page.url)

        logger.info(
            "SCREENSHOT TEST: viewport=%s",
            page.viewport_size,
        )

        logger.info(
            "SCREENSHOT TEST: title=%s",
            page.title(timeout=3000),
        )

        page.screenshot(
            path=QR_SCREENSHOT,
            full_page=False,
            timeout=5000,
        )

        last_screenshot_time = time.time()

        logger.info(
            "SCREENSHOT TEST: ERFOLGREICH -> %s",
            QR_SCREENSHOT,
        )

        return True

    except Exception as e:
        logger.error(
            "SCREENSHOT TEST: FEHLER -> %s",
            e,
            exc_info=True,
        )

        return False
# ============================================================
# TEXT NORMALISIERUNG
# ============================================================

def normalize_text(
    text,
):

    if text is None:

        return ""


    text = str(
        text
    )


    text = (
        text
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u200e", "")
        .replace("\u200f", "")
        .replace("\u202a", "")
        .replace("\u202b", "")
        .replace("\u202c", "")
        .strip()
    )


    lines = []


    for line in text.split(
        "\n"
    ):

        lines.append(
            " ".join(
                line.split()
            )
        )


    normalized_lines = []


    previous_empty = False


    for line in lines:

        if line == "":

            if previous_empty:

                continue


            previous_empty = True


            normalized_lines.append(
                ""
            )


        else:

            previous_empty = False


            normalized_lines.append(
                line
            )


    return "\n".join(
        normalized_lines
    ).strip()


def text_matches(
    expected,
    actual,
):

    expected_norm = normalize_text(
        expected
    )


    actual_norm = normalize_text(
        actual
    )


    if (
        expected_norm
        == actual_norm
    ):

        return True


    expected_flat = " ".join(
        expected_norm.split()
    )


    actual_flat = " ".join(
        actual_norm.split()
    )


    return (
        expected_flat
        == actual_flat
    )


# ============================================================
# NACHRICHTENTEXT LESEN
# ============================================================

def read_message_text(
    element,
):

    if element is None:

        return ""


    selectable_selectors = [

        '[data-testid="selectable-text"]',

        'span[data-testid="selectable-text"]',
    ]


    selectable_elements = []


    for selector in selectable_selectors:

        try:

            locator = element.locator(
                selector
            )


            count = locator.count()


            for i in range(
                min(count, 30)
            ):

                try:

                    item = locator.nth(
                        i
                    )


                    if not item.is_visible(
                        timeout=300
                    ):

                        continue


                    selectable_elements.append(
                        item
                    )


                except Exception:
                    pass


        except Exception:
            pass


    unique_elements = []


    seen_signatures = set()


    for item in selectable_elements:

        try:

            signature = item.evaluate(
                """
                (el) => {
                    return [
                        el.tagName || "",
                        el.getAttribute("data-testid") || "",
                        el.innerText || "",
                        el.textContent || ""
                    ].join("|");
                }
                """
            )


            if signature in seen_signatures:

                continue


            seen_signatures.add(
                signature
            )


            unique_elements.append(
                item
            )


        except Exception:

            unique_elements.append(
                item
            )


    if unique_elements:

        extracted_parts = []


        for item in unique_elements:

            try:

                value = item.evaluate(
                    """
                    (root) => {

                        function cleanInvisible(value) {
                            return String(value || "")
                                .replace(/\\u200e/g, "")
                                .replace(/\\u200f/g, "")
                                .replace(/\\u202a/g, "")
                                .replace(/\\u202b/g, "")
                                .replace(/\\u202c/g, "");
                        }

                        function getAccessible(node) {

                            if (!node) {
                                return "";
                            }

                            const alt =
                                node.getAttribute &&
                                node.getAttribute("alt");

                            if (alt) {
                                return alt;
                            }

                            const aria =
                                node.getAttribute &&
                                node.getAttribute("aria-label");

                            if (aria) {
                                return aria;
                            }

                            const title =
                                node.getAttribute &&
                                node.getAttribute("title");

                            if (title) {
                                return title;
                            }

                            return "";
                        }

                        function walk(node) {

                            if (!node) {
                                return "";
                            }

                            if (
                                node.nodeType ===
                                Node.TEXT_NODE
                            ) {
                                return cleanInvisible(
                                    node.nodeValue || ""
                                );
                            }

                            if (
                                node.nodeType !==
                                Node.ELEMENT_NODE
                            ) {
                                return "";
                            }

                            const tag =
                                String(
                                    node.tagName || ""
                                ).toLowerCase();

                            if (tag === "br") {
                                return "\\n";
                            }

                            if (
                                tag === "img"
                                || tag === "svg"
                            ) {
                                return getAccessible(
                                    node
                                );
                            }

                            let result = "";

                            for (
                                const child
                                of node.childNodes
                            ) {
                                result += walk(child);
                            }

                            return result;
                        }

                        return walk(root);
                    }
                    """
                )


                if value:

                    extracted_parts.append(
                        value
                    )


            except Exception as e:

                logger.debug(
                    "DOM-Textauslese fehlgeschlagen: %s",
                    e,
                )


        cleaned_parts = []


        for part in extracted_parts:

            normalized = normalize_text(
                part
            )


            if not normalized:

                continue


            duplicate = False


            for existing in cleaned_parts:

                existing_normalized = (
                    normalize_text(
                        existing
                    )
                )


                if (
                    normalized
                    == existing_normalized
                ):

                    duplicate = True

                    break


                if (
                    normalized
                    in existing_normalized
                ):

                    duplicate = True

                    break


                if (
                    existing_normalized
                    in normalized
                ):

                    duplicate = True

                    break


            if not duplicate:

                cleaned_parts.append(
                    part
                )


        if cleaned_parts:

            result = "\n".join(
                cleaned_parts
            )


            result = normalize_text(
                result
            )


            logger.info(
                "Nachrichtentext aus selectable-text DOM gelesen."
            )


            logger.info(
                "Gelesene Textlänge: %s Zeichen.",
                len(result),
            )


            return result


    try:

        fallback_text = element.inner_text(
            timeout=1000
        )


        if fallback_text:

            fallback_text = normalize_text(
                fallback_text
            )


            fallback_text = re.sub(
                r"\n?\s*\d{1,2}:\d{2}\s*$",
                "",
                fallback_text,
            ).strip()


            logger.info(
                "Nachrichtentext über Container-Fallback gelesen."
            )


            logger.info(
                "Gelesene Textlänge: %s Zeichen.",
                len(fallback_text),
            )


            return fallback_text


    except Exception as e:

        logger.debug(
            "Container-Fallback fehlgeschlagen: %s",
            e,
        )


    return ""


# ============================================================
# NACHRICHTEN-CONTAINER FINDEN
# ============================================================

def find_message_container_from_time_element(
    page,
    time_element,
):

    if time_element is None:

        return None


    try:

        data_id_locator = time_element.locator(
            "xpath=ancestor::*[@data-id][1]"
        )


        if data_id_locator.count() > 0:

            try:

                element = data_id_locator.first


                if element.is_visible(
                    timeout=300
                ):

                    data_id = (
                        element.get_attribute(
                            "data-id"
                        )
                        or ""
                    )


                    if data_id:

                        return element


            except Exception:
                pass


        ancestors = time_element.locator(
            "xpath=ancestor::*"
        )


        count = ancestors.count()


        if count == 0:

            return None


        candidates = []


        for i in range(
            min(count, 20)
        ):

            try:

                element = ancestors.nth(
                    i
                )


                if not element.is_visible(
                    timeout=200
                ):

                    continue


                data_id = (
                    element.get_attribute(
                        "data-id"
                    )
                    or ""
                )


                role = (
                    element.get_attribute(
                        "role"
                    )
                    or ""
                )


                class_name = (
                    element.get_attribute(
                        "class"
                    )
                    or ""
                )


                class_lower = class_name.lower()


                tag_name = ""


                try:

                    tag_name = (
                        element.evaluate(
                            "(el) => el.tagName"
                        )
                        or ""
                    ).lower()


                except Exception:
                    pass


                has_pre_plain = False


                try:

                    own_pre = (
                        element.get_attribute(
                            "data-pre-plain-text"
                        )
                        or ""
                    )


                    if own_pre:

                        has_pre_plain = True


                except Exception:
                    pass


                if not has_pre_plain:

                    try:

                        descendant_pre = element.locator(
                            "[data-pre-plain-text]"
                        )


                        if (
                            descendant_pre.count()
                            > 0
                        ):

                            has_pre_plain = True


                    except Exception:
                        pass


                text_length = 0


                try:

                    element_text = (
                        element.inner_text(
                            timeout=300
                        )
                        or ""
                    )


                    text_length = len(
                        element_text
                    )


                except Exception:
                    pass


                score = 0


                if data_id:

                    score += 100


                if role.lower() == "row":

                    score += 40


                if "message" in class_lower:

                    score += 30


                if "bubble" in class_lower:

                    score += 25


                if "copyable-text" in class_lower:

                    score += 20


                if "focusable-list-item" in class_lower:

                    score += 20


                if "selectable-text" in class_lower:

                    score += 10


                if has_pre_plain:

                    score += 20


                if text_length > 0:

                    score += 5


                if text_length > 10000:

                    score -= 80

                elif text_length > 5000:

                    score -= 40

                elif text_length > 2000:

                    score -= 20


                if tag_name in [
                    "html",
                    "body",
                    "main",
                ]:

                    score -= 100


                candidates.append({

                    "element":
                        element,

                    "score":
                        score,

                    "data_id":
                        data_id,

                    "role":
                        role,

                    "class":
                        class_name,

                    "tag":
                        tag_name,

                    "text_length":
                        text_length,
                })


            except Exception:
                pass


        if not candidates:

            return None


        candidates.sort(
            key=lambda item:
                item["score"],
            reverse=True,
        )


        selected = candidates[0]


        logger.info(
            "Nachrichten-Container gefunden: "
            "score=%s data-id=%r",
            selected["score"],
            selected["data_id"],
        )


        return selected[
            "element"
        ]


    except Exception as e:

        logger.debug(
            "Nachrichten-Container konnte nicht gefunden werden: %s",
            e,
        )


        return None


# ============================================================
# ZEITINFORMATIONEN
# ============================================================

def get_message_time_values(
    element,
):

    values = []


    if element is None:

        return values


    direct_attributes = [

        "data-pre-plain-text",

        "title",

        "aria-label",
    ]


    for attribute in direct_attributes:

        try:

            value = (
                element.get_attribute(
                    attribute
                )
                or ""
            )


            if value:

                values.append(
                    value
                )


        except Exception:
            pass


    selectors = [

        "[data-pre-plain-text]",

        "[title]",

        "[aria-label]",
    ]


    for selector in selectors:

        try:

            locator = element.locator(
                selector
            )


            count = locator.count()


            for i in range(
                min(count, 50)
            ):

                try:

                    item = locator.nth(
                        i
                    )


                    value = (
                        item.get_attribute(
                            "data-pre-plain-text"
                        )
                        or ""
                    )


                    if value:

                        values.append(
                            value
                        )


                    for attribute in [
                        "title",
                        "aria-label",
                    ]:

                        value = (
                            item.get_attribute(
                                attribute
                            )
                            or ""
                        )


                        if value:

                            values.append(
                                value
                            )


                except Exception:
                    pass


        except Exception:
            pass


    return list(
        dict.fromkeys(
            values
        )
    )


def get_message_time_info(
    element,
):

    values = get_message_time_values(
        element
    )


    if not values:

        return ""


    return " | ".join(
        values
    )


# ============================================================
# ZEITSTEMPEL PARSEN
# ============================================================

def parse_message_timestamp(
    page,
    element,
    reference_time_ms,
):

    values = get_message_time_values(
        element
    )


    if not values:

        return None


    try:

        result = page.evaluate(
            """
            ({values, referenceMs}) => {

                function clean(value) {

                    if (!value) {
                        return "";
                    }

                    return String(value)
                        .replace(/\\u200e/g, "")
                        .replace(/\\u200f/g, "")
                        .replace(/\\u202a/g, "")
                        .replace(/\\u202b/g, "")
                        .replace(/\\u202c/g, "")
                        .trim();
                }


                function addCandidate(
                    result,
                    year,
                    month,
                    day,
                    hour,
                    minute,
                    source
                ) {

                    const date = new Date(
                        year,
                        month - 1,
                        day,
                        hour,
                        minute,
                        0,
                        0
                    );


                    if (
                        !Number.isNaN(
                            date.getTime()
                        )
                    ) {

                        result.push({

                            timestamp:
                                date.getTime(),

                            source:
                                source
                        });
                    }
                }


                function candidatesFromText(
                    text
                ) {

                    const result = [];


                    if (!text) {
                        return result;
                    }


                    const value =
                        clean(text);


                    let match =
                        value.match(
                            /(?:\\[)?(\\d{1,2}):(\\d{2})(?:\\s*([AaPp][Mm]))?\\s*,\\s*(\\d{1,2})[.\\/-](\\d{1,2})[.\\/-](\\d{2,4})/
                        );


                    if (match) {

                        let hour =
                            Number(match[1]);

                        const minute =
                            Number(match[2]);

                        const ampm =
                            match[3];

                        const day =
                            Number(match[4]);

                        const month =
                            Number(match[5]);

                        let year =
                            Number(match[6]);


                        if (year < 100) {
                            year += 2000;
                        }


                        if (ampm) {

                            if (
                                ampm.toLowerCase()
                                === "pm"
                                && hour < 12
                            ) {

                                hour += 12;
                            }


                            if (
                                ampm.toLowerCase()
                                === "am"
                                && hour === 12
                            ) {

                                hour = 0;
                            }
                        }


                        addCandidate(
                            result,
                            year,
                            month,
                            day,
                            hour,
                            minute,
                            value
                        );
                    }


                    match =
                        value.match(
                            /(?:\\[)?(\\d{1,2})[.\\/-](\\d{1,2})[.\\/-](\\d{2,4})\\s*,\\s*(\\d{1,2}):(\\d{2})(?:\\s*([AaPp][Mm]))?/
                        );


                    if (match) {

                        const day =
                            Number(match[1]);

                        const month =
                            Number(match[2]);

                        let year =
                            Number(match[3]);

                        let hour =
                            Number(match[4]);

                        const minute =
                            Number(match[5]);

                        const ampm =
                            match[6];


                        if (year < 100) {
                            year += 2000;
                        }


                        if (ampm) {

                            if (
                                ampm.toLowerCase()
                                === "pm"
                                && hour < 12
                            ) {

                                hour += 12;
                            }


                            if (
                                ampm.toLowerCase()
                                === "am"
                                && hour === 12
                            ) {

                                hour = 0;
                            }
                        }


                        addCandidate(
                            result,
                            year,
                            month,
                            day,
                            hour,
                            minute,
                            value
                        );
                    }


                    const timeMatches =
                        value.matchAll(
                            /(?:^|[^0-9])(\\d{1,2}):(\\d{2})(?:\\s*([AaPp][Mm]))?(?:$|[^0-9])/g
                        );


                    for (
                        const item
                        of timeMatches
                    ) {

                        let hour =
                            Number(item[1]);

                        const minute =
                            Number(item[2]);

                        const ampm =
                            item[3];


                        if (ampm) {

                            if (
                                ampm.toLowerCase()
                                === "pm"
                                && hour < 12
                            ) {

                                hour += 12;
                            }


                            if (
                                ampm.toLowerCase()
                                === "am"
                                && hour === 12
                            ) {

                                hour = 0;
                            }
                        }


                        const now =
                            new Date(
                                referenceMs
                            );


                        const dates = [

                            new Date(
                                now.getFullYear(),
                                now.getMonth(),
                                now.getDate(),
                                hour,
                                minute,
                                0,
                                0
                            ),

                            new Date(
                                now.getFullYear(),
                                now.getMonth(),
                                now.getDate() - 1,
                                hour,
                                minute,
                                0,
                                0
                            ),

                            new Date(
                                now.getFullYear(),
                                now.getMonth(),
                                now.getDate() + 1,
                                hour,
                                minute,
                                0,
                                0
                            )
                        ];


                        for (
                            const date
                            of dates
                        ) {

                            if (
                                !Number.isNaN(
                                    date.getTime()
                                )
                            ) {

                                result.push({

                                    timestamp:
                                        date.getTime(),

                                    source:
                                        value
                                });
                            }
                        }
                    }


                    return result;
                }


                const candidates = [];


                for (
                    const value
                    of values
                ) {

                    const parsed =
                        candidatesFromText(
                            value
                        );


                    for (
                        const candidate
                        of parsed
                    ) {

                        candidates.push(
                            candidate
                        );
                    }
                }


                if (!candidates.length) {
                    return null;
                }


                candidates.sort(
                    (a, b) => {

                        return Math.abs(
                            a.timestamp
                            - referenceMs
                        )
                        -
                        Math.abs(
                            b.timestamp
                            - referenceMs
                        );
                    }
                );


                return candidates[0];
            }
            """,
            {
                "values":
                    values,

                "referenceMs":
                    reference_time_ms,
            },
        )


        if not result:

            return None


        timestamp = result.get(
            "timestamp"
        )


        source = result.get(
            "source",
            "",
        )


        if not timestamp:

            return None


        return {

            "timestamp_ms":
                float(timestamp),

            "source":
                source,
        }


    except Exception as e:

        logger.debug(
            "Nachrichtenzeit konnte nicht geparst werden: %s",
            e,
        )


        return None


# ============================================================
# BROWSER ZEIT FORMATIEREN
# ============================================================

def format_browser_timestamp(
    page,
    timestamp_ms,
):

    try:

        result = page.evaluate(
            """
            (timestampMs) => {

                const date =
                    new Date(timestampMs);

                const pad =
                    value =>
                        String(value)
                            .padStart(2, "0");

                return (
                    pad(date.getDate())
                    + "."
                    + pad(date.getMonth() + 1)
                    + "."
                    + date.getFullYear()
                    + " "
                    + pad(date.getHours())
                    + ":"
                    + pad(date.getMinutes())
                    + ":"
                    + pad(date.getSeconds())
                );
            }
            """,
            timestamp_ms,
        )


        return result


    except Exception:

        return str(
            timestamp_ms
        )


# ============================================================
# ZEIT-KANDIDATEN
# ============================================================

def get_all_time_candidates(
    page,
):

    candidates = []


    selector = (
        "[data-pre-plain-text]"
    )


    try:

        locator = page.locator(
            selector
        )


        count = locator.count()


        if count == 0:

            return []


        start = max(
            0,
            count - 300,
        )


        for i in range(
            start,
            count
        ):

            try:

                element = locator.nth(
                    i
                )


                if not element.is_visible(
                    timeout=200
                ):

                    continue


                pre_plain = (
                    element.get_attribute(
                        "data-pre-plain-text"
                    )
                    or ""
                )


                if not pre_plain:

                    continue


                title = (
                    element.get_attribute(
                        "title"
                    )
                    or ""
                )


                aria = (
                    element.get_attribute(
                        "aria-label"
                    )
                    or ""
                )


                container = (
                    find_message_container_from_time_element(
                        page,
                        element,
                    )
                )


                message_data_id = ""


                message_class = ""


                message_role = ""


                if container:

                    message_data_id = (
                        container.get_attribute(
                            "data-id"
                        )
                        or ""
                    )


                    message_class = (
                        container.get_attribute(
                            "class"
                        )
                        or ""
                    )


                    message_role = (
                        container.get_attribute(
                            "role"
                        )
                        or ""
                    )


                candidates.append({

                    "element":
                        element,

                    "container":
                        container,

                    "pre_plain":
                        pre_plain,

                    "title":
                        title,

                    "aria":
                        aria,

                    "message_data_id":
                        message_data_id,

                    "message_class":
                        message_class,

                    "message_role":
                        message_role,
                })


            except Exception:
                pass


    except Exception as e:

        logger.debug(
            "Zeitkandidaten konnten nicht gelesen werden: %s",
            e,
        )


    return candidates


# ============================================================
# ZEIT-SNAPSHOT
# ============================================================

def snapshot_time_candidates(
    page,
):

    result = []


    candidates = get_all_time_candidates(
        page
    )


    for candidate in candidates:

        result.append({

            "pre_plain":
                candidate.get(
                    "pre_plain",
                    "",
                ),

            "title":
                candidate.get(
                    "title",
                    "",
                ),

            "aria":
                candidate.get(
                    "aria",
                    "",
                ),

            "message_data_id":
                candidate.get(
                    "message_data_id",
                    "",
                ),

            "message_class":
                candidate.get(
                    "message_class",
                    "",
                ),

            "message_role":
                candidate.get(
                    "message_role",
                    "",
                ),
        })


    logger.info(
        "Zeit-Diagnostik vor dem Senden: %s sichtbare "
        "Nachrichten-Zeit-Elemente gefunden.",
        len(result),
    )


    return result


# ============================================================
# NEUER ZEIT-KANDIDAT
# ============================================================

def is_new_time_candidate(
    candidate,
    before_time_candidates,
):

    current_data_id = (
        candidate.get(
            "message_data_id",
            "",
        )
        or ""
    )


    current_pre_plain = (
        candidate.get(
            "pre_plain",
            "",
        )
        or ""
    )


    current_class = (
        candidate.get(
            "message_class",
            "",
        )
        or ""
    )


    current_role = (
        candidate.get(
            "message_role",
            "",
        )
        or ""
    )


    if current_data_id:

        for before in before_time_candidates:

            before_data_id = (
                before.get(
                    "message_data_id",
                    "",
                )
                or ""
            )


            if (
                before_data_id
                and before_data_id
                == current_data_id
            ):

                return False


        return True


    current_key = (

        current_pre_plain,

        current_class,

        current_role,
    )


    for before in before_time_candidates:

        before_key = (

            before.get(
                "pre_plain",
                "",
            )
            or "",

            before.get(
                "message_class",
                "",
            )
            or "",

            before.get(
                "message_role",
                "",
            )
            or "",
        )


        if (
            current_key
            == before_key
        ):

            return False


    return True


# ============================================================
# NEUE NACHRICHT FINDEN
# ============================================================

def find_new_time_message(
    page,
    before_time_candidates,
):

    candidates = get_all_time_candidates(
        page
    )


    new_candidates = []


    for candidate in candidates:

        if not is_new_time_candidate(
            candidate,
            before_time_candidates,
        ):

            continue


        new_candidates.append(
            candidate
        )


    if not new_candidates:

        return None


    selected = new_candidates[-1]


    logger.info(
        "Es wurden %s neue Zeit-Kandidaten gefunden.",
        len(new_candidates),
    )


    return selected


# ============================================================
# KONTAKT UNGEKLÄRT
# ============================================================

def set_contact_unclear(
    conn,
    order,
    kontakte,
    index,
    error_code,
    error_message,
    phase=None,
):

    kontakt = kontakte[index]


    kontakt[
        "_send_status"
    ] = "ungeklärt"


    kontakt[
        "_send_error"
    ] = error_message


    kontakt[
        "_send_error_code"
    ] = error_code


    if phase:

        kontakt[
            "_send_phase"
        ] = phase


    kontakt[
        "_send_unclear_at"
    ] = time.strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


    kontakt[
        "_send_attempted_at"
    ] = kontakt.get(
        "_send_attempted_at"
    ) or time.strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


    logger.error(
        "SEND-FEHLER [%s]: %s",
        error_code,
        error_message,
    )


    logger.error(
        "Kontakt %s wurde auf 'ungeklärt' gesetzt.",
        index + 1,
    )


    logger.error(
        "Phase: %s",
        kontakt.get(
            "_send_phase",
            "unbekannt",
        ),
    )


    logger.error(
        "Es erfolgt KEIN automatischer erneuter Versand."
    )


    update_order(
        conn,
        order["id"],
        kontakte=kontakte,
    )


# ============================================================
# KONTAKT BESTÄTIGT
# ============================================================

def set_contact_confirmed(
    conn,
    order,
    kontakte,
    index,
):

    kontakt = kontakte[index]


    kontakt[
        "_send_status"
    ] = "bestätigt"


    kontakt[
        "_send_phase"
    ] = "bestätigt"


    kontakt.pop(
        "_send_error",
        None,
    )


    kontakt.pop(
        "_send_error_code",
        None,
    )


    kontakt.pop(
        "_send_unclear_at",
        None,
    )


    kontakt[
        "_send_confirmed_at"
    ] = time.strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


    update_order(
        conn,
        order["id"],
        kontakte=kontakte,
    )


# ============================================================
# VERSANDPRÜFUNG
# ============================================================

def verify_sent_message(
    page,
    before_time_candidates,
    expected_text,
    send_time,
):

    logger.info(
        "=================================================="
    )


    logger.info(
        "STARTE STRIKTE VERSANDPRÜFUNG"
    )


    logger.info(
        "PHASE 1 -> neue Nachricht über Zeitanker finden"
    )


    logger.info(
        "PHASE 2 -> Zeit dieser Nachricht prüfen"
    )


    logger.info(
        "PHASE 3 -> Text dieser Nachricht prüfen"
    )


    logger.info(
        "=================================================="
    )


    send_time_ms = (
        float(send_time)
        * 1000.0
    )


    send_time_readable = (
        format_browser_timestamp(
            page,
            send_time_ms,
        )
    )


    logger.info(
        "Sendezeit Browser: %s",
        send_time_readable,
    )


    deadline = (
        time.time()
        + VERIFY_TIMEOUT_SECONDS
    )


    already_logged_candidates = set()


    while (
        time.time()
        < deadline
    ):

        try:

            close_whatsapp_popups(
                page
            )


            candidate = (
                find_new_time_message(
                    page,
                    before_time_candidates,
                )
            )


            if candidate is None:

                time.sleep(
                    VERIFY_POLL_SECONDS
                )

                continue


            pre_plain = (
                candidate.get(
                    "pre_plain",
                    "",
                )
            )


            message_data_id = (
                candidate.get(
                    "message_data_id",
                    "",
                )
            )


            message_class = (
                candidate.get(
                    "message_class",
                    "",
                )
            )


            message_role = (
                candidate.get(
                    "message_role",
                    "",
                )
            )


            candidate_key = (
                message_data_id
                or (
                    pre_plain
                    + "|"
                    + message_class
                    + "|"
                    + message_role
                )
            )


            if (
                candidate_key
                not in already_logged_candidates
            ):

                already_logged_candidates.add(
                    candidate_key
                )


                logger.info(
                    "Neuer Zeitanker gefunden."
                )


                logger.info(
                    "data-pre-plain-text=%r",
                    pre_plain,
                )


                logger.info(
                    "Nachrichten-data-id=%r",
                    message_data_id,
                )


            message_element = (
                candidate.get(
                    "container"
                )
            )


            if message_element is None:

                return (

                    False,

                    "ZEIT_CONTAINER_NICHT_GEFUNDEN",

                    "Neuer Nachrichten-Zeitanker gefunden, aber der zugehörige Nachrichten-Container konnte nicht bestimmt werden.",

                    "verifikation_neue_nachricht",
                )


            # =================================================
            # PHASE 2
            # =================================================

            time_info = (
                get_message_time_info(
                    message_element
                )
            )


            if not time_info:

                time.sleep(
                    VERIFY_POLL_SECONDS
                )

                continue


            parsed_time = (
                parse_message_timestamp(
                    page,
                    message_element,
                    send_time_ms,
                )
            )


            if not parsed_time:

                return (

                    False,

                    "NACHRICHTENZEIT_NICHT_LESBAR",

                    "Neue Nachricht gefunden, aber ihre Nachrichtenzeit konnte nicht interpretiert werden.",

                    "nachrichtenzeit",
                )


            message_timestamp_ms = (
                parsed_time[
                    "timestamp_ms"
                ]
            )


            message_time_readable = (
                format_browser_timestamp(
                    page,
                    message_timestamp_ms,
                )
            )


            difference_seconds = abs(
                message_timestamp_ms
                - send_time_ms
            ) / 1000.0


            logger.info(
                "WhatsApp-Nachrichtenzeit: %s",
                message_time_readable,
            )


            logger.info(
                "Zeitquelle: %r",
                parsed_time.get(
                    "source",
                    "",
                ),
            )


            logger.info(
                "Zeitdifferenz zu Enter: %.1f Sekunden.",
                difference_seconds,
            )


            if (
                difference_seconds
                > VERIFY_TIME_TOLERANCE_SECONDS
            ):

                return (

                    False,

                    "NACHRICHTENZEIT_PASST_NICHT",

                    "Neue Nachricht gefunden, aber ihre Nachrichtenzeit passt nicht zum Sendezeitpunkt.",

                    "nachrichtenzeit",
                )


            logger.info(
                "PHASE 2 ERFOLGREICH: Nachrichtenzeit passt."
            )


            # =================================================
            # PHASE 3
            # =================================================

            actual_text = normalize_text(
                read_message_text(
                    message_element
                )
            )


            expected_text_normalized = (
                normalize_text(
                    expected_text
                )
            )


            logger.info(
                "Erwarteter Text: %r",
                expected_text_normalized,
            )


            logger.info(
                "Gefundener Text: %r",
                actual_text,
            )


            if not actual_text:

                return (

                    False,

                    "NACHRICHTENTEXT_NICHT_LESBAR",

                    "Zeit der neuen Nachricht passt, aber der Text dieser exakt gefundenen Nachricht konnte nicht gelesen werden.",

                    "nachrichtentext",
                )


            if text_matches(
                expected_text_normalized,
                actual_text,
            ):

                logger.info(
                    "PHASE 3 ERFOLGREICH: Text passt."
                )


                logger.info(
                    "VERSAND VOLLSTÄNDIG BESTÄTIGT."
                )


                return (

                    True,

                    "BESTAETIGT",

                    "Neue Nachricht gefunden, Zeit passt und der Text der exakt gefundenen Nachricht stimmt.",

                    "bestätigt",
                )


            return (

                False,

                "NACHRICHTENTEXT_PASST_NICHT",

                "Zeit der neuen Nachricht passt, aber der Text der exakt gefundenen neuen Nachricht stimmt nicht.",

                "nachrichtentext",
            )


        except Exception as e:

            logger.exception(
                "Fehler während der Versandprüfung: %s",
                e,
            )


            return (

                False,

                "VERIFIKATION_AUSNAHME",

                f"Unerwarteter Fehler während der Versandprüfung: {e}",

                "verifikation_neue_nachricht",
            )


        time.sleep(
            VERIFY_POLL_SECONDS
        )


    return (

        False,

        "KEINE_NEUE_NACHRICHT_GEFUNDEN",

        f"Innerhalb von {VERIFY_TIMEOUT_SECONDS} Sekunden wurde keine neue Nachricht über einen neuen Zeitanker gefunden.",

        "verifikation_neue_nachricht",
    )


# ============================================================
# CHAT ÖFFNEN
# ============================================================

def open_chat(
    page,
    phone,
):

    url = (
        "https://web.whatsapp.com/send"
        f"?phone={phone}"
    )

    logger.info(
        "Öffne WhatsApp-Chat: %s",
        phone,
    )

    close_whatsapp_popups(
        page
    )

    compose_selectors = [

        '[contenteditable="true"][role="textbox"]',

        '[contenteditable="true"][data-tab]',
    ]

    deadline = (
        time.time()
        + 30
    )

    while (
        time.time()
        < deadline
    ):

        close_whatsapp_popups(
            page
        )

        for selector in compose_selectors:

            try:

                locator = page.locator(
                    selector
                )

                count = locator.count()

                for i in range(
                    count
                ):

                    try:

                        element = locator.nth(
                            i
                        )

                        if element.is_visible(
                            timeout=500
                        ):

                            return element

                    except Exception:
                        pass

            except Exception:
                pass

        time.sleep(
            0.5
        )

    raise RuntimeError(
        "Nachrichtenfeld wurde nach "
        "30 Sekunden nicht gefunden."
    )


# ============================================================
# KONTAKT VERARBEITEN
# ============================================================

def process_contact(
    page,
    conn,
    order,
    kontakte,
    index,
):

    global current_contact_index


    current_contact_index = (
        index + 1
    )


    kontakt = kontakte[index]


    telefon = kontakt.get(
        "telefon"
    )


    nachricht = kontakt.get(
        "nachricht",
        "",
    )


    phone = normalize_phone(
        telefon
    )


    if not phone:

        set_contact_unclear(
            conn,
            order,
            kontakte,
            index,
            "TELEFONNUMMER_UNGUELTIG",
            "Kontakt enthält keine gültige Telefonnummer.",
            "chat_oeffnen",
        )


        return False


    logger.info(
        "KONTAKT %s/%s | Telefon: %s",
        index + 1,
        len(kontakte),
        phone,
    )


    if (
        kontakt.get(
            "_send_status"
        )
        == "bestätigt"
    ):

        logger.info(
            "Kontakt bereits bestätigt. Überspringe."
        )


        return True


    if (
        kontakt.get(
            "_send_status"
        )
        in [
            "sending",
            "ungeklärt",
            "fehler",
        ]
    ):

        logger.warning(
            "Kontakt hat Status '%s'. "
            "Kein automatischer erneuter Versand.",
            kontakt.get(
                "_send_status"
            ),
        )


        return False


    set_contact_phase(
        conn,
        order,
        kontakte,
        index,
        "chat_oeffnen",
    )


    try:

        compose = open_chat(
            page,
            phone,
        )


    except Exception as e:

        logger.error(
            "Chat konnte nicht geöffnet werden: %s",
            e,
        )


        set_contact_unclear(
            conn,
            order,
            kontakte,
            index,
            "CHAT_KONNTE_NICHT_GEOEFFNET_WERDEN",
            f"WhatsApp-Chat konnte nicht geöffnet werden: {e}",
            "chat_oeffnen",
        )


        return False


    set_contact_phase(
        conn,
        order,
        kontakte,
        index,
        "eingabe",
    )


    try:

        compose.click()


        compose.fill(
            str(nachricht)
        )


    except Exception as e:

        logger.error(
            "Nachricht konnte nicht eingefügt werden: %s",
            e,
        )


        set_contact_unclear(
            conn,
            order,
            kontakte,
            index,
            "NACHRICHT_KONNTE_NICHT_EINGEFUEGT_WERDEN",
            f"Nachricht konnte nicht in das WhatsApp-Eingabefeld eingefügt werden: {e}",
            "eingabe",
        )


        return False


    try:

        actual_input = compose.inner_text(
            timeout=2000
        )


    except Exception:

        try:

            actual_input = compose.input_value(
                timeout=2000
            )


        except Exception:

            actual_input = ""


    if not text_matches(
        nachricht,
        actual_input,
    ):

        logger.error(
            "Eingabeprüfung fehlgeschlagen."
        )


        set_contact_unclear(
            conn,
            order,
            kontakte,
            index,
            "EINGABEPRUEFUNG_FEHLGESCHLAGEN",
            "Der Text im WhatsApp-Eingabefeld entspricht nicht der erwarteten Nachricht.",
            "eingabe",
        )


        return False


    logger.info(
        "Nachricht korrekt im Eingabefeld."
    )


    set_contact_phase(
        conn,
        order,
        kontakte,
        index,
        "eingabe_geprueft",
    )


    before_time_candidates = (
        snapshot_time_candidates(
            page
        )
    )


    if (
        DRY_RUN
        or not ALLOW_REAL_SEND
    ):

        logger.info(
            "DRY RUN / REAL SEND deaktiviert. "
            "Nachricht wird NICHT gesendet."
        )


        kontakt[
            "_send_status"
        ] = "trockenlauf"


        kontakt[
            "_send_phase"
        ] = "dry_run"


        update_order(
            conn,
            order["id"],
            kontakte=kontakte,
        )


        return True


    kontakt[
        "_send_status"
    ] = "sending"


    kontakt[
        "_send_phase"
    ] = "senden"


    kontakt[
        "_send_attempted_at"
    ] = time.strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


    kontakt.pop(
        "_send_error",
        None,
    )


    kontakt.pop(
        "_send_error_code",
        None,
    )


    kontakt.pop(
        "_send_unclear_at",
        None,
    )


    update_order(
        conn,
        order["id"],
        kontakte=kontakte,
    )


    try:

        send_time_ms = page.evaluate(
            "() => Date.now()"
        )


        send_time = (
            float(
                send_time_ms
            )
            / 1000.0
        )


    except Exception as e:

        set_contact_unclear(
            conn,
            order,
            kontakte,
            index,
            "SENDEZEIT_KONNTE_NICHT_ERMITTELT_WERDEN",
            f"Der Sendezeitpunkt konnte vor dem Versand nicht ermittelt werden: {e}",
            "senden",
        )


        return False


    logger.info(
        "Sendezeitpunkt VOR Enter: %.3f",
        send_time,
    )


    try:

        compose.press(
            "Enter"
        )


        logger.info(
            "Enter wurde einmal gedrückt."
        )


    except Exception as e:

        set_contact_unclear(
            conn,
            order,
            kontakte,
            index,
            "ENTER_FEHLGESCHLAGEN",
            f"Die Nachricht konnte nicht mit Enter gesendet werden: {e}",
            "senden",
        )


        return False


    logger.info(
        "Nachricht wurde einmal ausgelöst."
    )


    set_contact_phase(
        conn,
        order,
        kontakte,
        index,
        "verifikation_neue_nachricht",
    )


    (
        verified,
        error_code,
        verification_message,
        error_phase,
    ) = verify_sent_message(
        page,
        before_time_candidates,
        nachricht,
        send_time,
    )


    if verified:

        logger.info(
            "Nachricht vollständig bestätigt."
        )


        set_contact_confirmed(
            conn,
            order,
            kontakte,
            index,
        )


        return True


    logger.error(
        "Nachricht konnte nicht vollständig bestätigt werden."
    )


    logger.error(
        "Fehlercode: %s",
        error_code,
    )


    logger.error(
        "KEIN RESEND."
    )


    set_contact_unclear(
        conn,
        order,
        kontakte,
        index,
        error_code,
        verification_message,
        error_phase,
    )


    return False


# ============================================================
# AUFTRAG VERARBEITEN
# ============================================================

def process_order(
    page,
    conn,
    order,
):

    global current_order_id
    global current_contact_index


    current_order_id = (
        order["id"]
    )


    logger.info(
        "Verarbeite Auftrag %s",
        order["id"],
    )


    kontakte = order[
        "kontakte"
    ]


    update_order(
        conn,
        order["id"],
        status="in_bearbeitung",
        kontakte=kontakte,
    )


    if not kontakte:

        logger.warning(
            "Auftrag enthält keine Kontakte."
        )


        update_order(
            conn,
            order["id"],
            status="fehler",
            kontakte=kontakte,
            gesendet=0,
            fehler=1,
        )


        current_order_id = None
        current_contact_index = None


        return False


    for index in range(
        len(kontakte)
    ):

        if shutdown_requested:

            break


        current_contact_index = (
            index + 1
        )


        kontakt = kontakte[index]


        status = kontakt.get(
            "_send_status"
        )


        if status == "bestätigt":

            continue


        if status in [
            "ungeklärt",
            "fehler",
            "sending",
        ]:

            logger.warning(
                "Kontakt %s hat bereits Status '%s'. "
                "Kein erneuter Versand.",
                index + 1,
                status,
            )


            continue


        try:

            success = process_contact(
                page,
                conn,
                order,
                kontakte,
                index,
            )


        except Exception as e:

            logger.exception(
                "Unerwarteter Fehler bei Kontakt %s: %s",
                index + 1,
                e,
            )


            try:

                set_contact_unclear(
                    conn,
                    order,
                    kontakte,
                    index,
                    "KONTAKT_AUSNAHME",
                    f"Unerwarteter Fehler bei der Verarbeitung des Kontakts: {e}",
                    kontakte[index].get(
                        "_send_phase",
                        "unbekannt",
                    ),
                )


            except Exception as inner_error:

                logger.exception(
                    "Fehler beim Speichern des ungeklärten Kontakts: %s",
                    inner_error,
                )


            success = False


        if success:

            logger.info(
                "Kontakt %s erfolgreich verarbeitet.",
                index + 1,
            )


        else:

            logger.warning(
                "Kontakt %s wurde NICHT bestätigt.",
                index + 1,
            )


            logger.warning(
                "KEIN RESEND."
            )


        time.sleep(
            CONTACT_DELAY_SECONDS
        )


    confirmed_count = 0

    unclear_count = 0

    other_count = 0


    for kontakt in kontakte:

        status = kontakt.get(
            "_send_status"
        )


        if status == "bestätigt":

            confirmed_count += 1


        elif status in [
            "ungeklärt",
            "fehler",
            "sending",
        ]:

            unclear_count += 1


        else:

            other_count += 1


    if (
        confirmed_count
        == len(kontakte)
        and len(kontakte)
        > 0
    ):

        update_order(
            conn,
            order["id"],
            status="abgeschlossen",
            kontakte=kontakte,
            gesendet=confirmed_count,
            fehler=0,
        )


        current_order_id = None
        current_contact_index = None


        return True


    if unclear_count > 0:

        update_order(
            conn,
            order["id"],
            status="fehler",
            kontakte=kontakte,
            gesendet=confirmed_count,
            fehler=unclear_count,
        )


        current_order_id = None
        current_contact_index = None


        return False


    update_order(
        conn,
        order["id"],
        status="fehler",
        kontakte=kontakte,
        gesendet=confirmed_count,
        fehler=max(
            1,
            unclear_count,
        ),
    )


    current_order_id = None
    current_contact_index = None


    return False


# ============================================================
# CHROMIUM LOCKS
# ============================================================

def cleanup_browser_locks():

    browser_path = Path(
        BROWSER_DIR
    ).absolute()


    lock_files = [

        "SingletonLock",

        "SingletonCookie",

        "SingletonSocket",
    ]


    removed = False


    for filename in lock_files:

        lock_path = (
            browser_path
            / filename
        )


        try:

            if lock_path.exists():

                lock_path.unlink()


                logger.warning(
                    "Alte Chromium-Lockdatei entfernt: %s",
                    lock_path,
                )


                removed = True


        except Exception as e:

            logger.warning(
                "Chromium-Lockdatei konnte nicht entfernt werden: "
                "%s | %s",
                lock_path,
                e,
            )


    if not removed:

        logger.info(
            "Keine alten Chromium-Lockdateien gefunden."
        )


# ============================================================
# BROWSER STARTEN
# ============================================================

def start_browser(
    playwright,
):

    global browser_page
    global login_status


    browser_path = Path(
        BROWSER_DIR
    )


    browser_path.mkdir(
        parents=True,
        exist_ok=True,
    )


    logger.info(
        "Browser-Profil: %s",
        browser_path.absolute(),
    )


    cleanup_browser_locks()


    launch_args = [

        "--disable-dev-shm-usage",

        "--no-sandbox",

        "--disable-blink-features=AutomationControlled",

        "--disable-gpu",

        "--no-first-run",

        "--no-default-browser-check",
    ]


    try:

        context = (
            playwright.chromium
            .launch_persistent_context(
                user_data_dir=str(
                    browser_path.absolute()
                ),

                headless=HEADLESS,

                no_viewport=True,

                args=launch_args,
            )
        )


        if context.pages:

            page = context.pages[0]

        else:

            page = context.new_page()


        browser_page = page

        login_status = "browser_started"


        logger.info(
            "Chromium erfolgreich gestartet."
        )


        return context


    except Exception as e:

        error_text = str(
            e
        )


        if (
            "SingletonLock"
            in error_text
            or "SingletonCookie"
            in error_text
            or "SingletonSocket"
            in error_text
            or "profile appears to be in use"
            in error_text
        ):

            logger.warning(
                "Chromium-Profil war gesperrt."
            )


            cleanup_browser_locks()


            time.sleep(
                1
            )


            context = (
                playwright.chromium
                .launch_persistent_context(
                    user_data_dir=str(
                        browser_path.absolute()
                    ),

                    headless=HEADLESS,

                    no_viewport=True,

                    args=launch_args,
                )
            )


            if context.pages:

                page = context.pages[0]

            else:

                page = context.new_page()


            browser_page = page

            login_status = (
                "browser_started"
            )


            logger.info(
                "Chromium nach Lock-Cleanup erfolgreich gestartet."
            )


            return context


        raise


# ============================================================
# BROWSER SCHLIESSEN
# ============================================================

def close_browser_context(
    context,
):

    global browser_page


    if not context:

        return


    try:

        context.close()


        logger.info(
            "Browser-Kontext geschlossen."
        )


    except Exception as e:

        logger.warning(
            "Browser konnte nicht sauber geschlossen werden: %s",
            e,
        )


    finally:

        browser_page = None

# ============================================================
# login
# ============================================================

def wait_for_login(
    page,
):
    global login_status

    logger.info(
        "Warte auf WhatsApp-Login..."
    )

    started = time.time()
    screenshot_number = 0

    while not shutdown_requested:
        screenshot_success = save_screenshot(page)
        screenshot_number += 1

        if (
            screenshot_number == 1
            and not screenshot_success
        ):
            logger.warning(
                "ERSTER QR-SCREENSHOT FEHLGESCHLAGEN."
            )

        try:
            close_whatsapp_popups(page)
        except Exception:
            pass

        try:
            if is_logged_in(page):
                login_status = "logged_in"
                logger.info(
                    "WhatsApp-Login erkannt."
                )
                return True

            login_status = "qr_ready"

            if screenshot_number % 10 == 0:
                logger.info(
                    "WhatsApp wartet noch auf Anmeldung. "
                    "Screenshot #%s | verfügbar=%s",
                    screenshot_number,
                    os.path.exists(QR_SCREENSHOT),
                )

        except Exception as e:
            logger.debug(
                "Loginprüfung Fehler: %s",
                e,
            )

        if LOGIN_TIMEOUT_SECONDS > 0:
            elapsed = time.time() - started

            if elapsed >= LOGIN_TIMEOUT_SECONDS:
                logger.error(
                    "Login-Timeout nach %s Sekunden.",
                    LOGIN_TIMEOUT_SECONDS,
                )
                return False

        time.sleep(1)

    return False

# ============================================================
# BROWSER SESSION AUFBAUEN
# ============================================================

def prepare_whatsapp_session(
    page,
):

    global login_status

    logger.info(
        "WHATSAPP GOTO START"
    )

    page.goto(
        "https://web.whatsapp.com/",
        wait_until="domcontentloaded",
        timeout=60000,
    )

    logger.info(
        "WHATSAPP GOTO ENDE"
    )

    logged_in = wait_for_login(
        page
    )

    logger.info(
        "WAIT_FOR_LOGIN ENDE: %s",
        logged_in,
    )

    if not logged_in:

        login_status = "error"

        raise RuntimeError(
            "WhatsApp konnte nicht angemeldet werden."
        )

    close_whatsapp_popups(
        page
    )

    login_status = (
        "logged_in"
    )

    logger.info(
        "WhatsApp ist bereit."
    )


# ============================================================
# HEARTBEAT
# ============================================================

def heartbeat_loop():

    global last_heartbeat_time


    while not shutdown_requested:

        try:

            last_heartbeat_time = (
                time.time()
            )


            logger.debug(
                "Worker-Heartbeat."
            )


        except Exception:
            pass


        time.sleep(
            HEARTBEAT_SECONDS
        )


# ============================================================
# EINEN WORKER-ZYKLUS
# ============================================================

def run_worker_cycle(
    playwright,
    conn,
    context,
    page,
):

    global browser_restart_requested
    global login_status


    if shutdown_requested:

        return conn, context, page


    # --------------------------------------------------------
    # DB-Verbindung prüfen
    # --------------------------------------------------------

    conn = ensure_db_connection(
        conn
    )


    if conn is None:

        raise RuntimeError(
            "Keine Datenbankverbindung verfügbar."
        )


    # --------------------------------------------------------
    # Browser prüfen
    # --------------------------------------------------------

    try:

        if page.is_closed():

            raise RuntimeError(
                "WhatsApp-Seite wurde geschlossen."
            )


    except Exception as e:

        logger.warning(
            "Browser-Seite nicht mehr verfügbar: %s",
            e,
        )


        browser_restart_requested = True


    if browser_restart_requested:

        logger.warning(
            "Browser-Neustart erforderlich."
        )


        close_browser_context(
            context
        )


        time.sleep(
            BROWSER_RESTART_DELAY_SECONDS
        )


        context = start_browser(
            playwright
        )


        if context.pages:

            page = context.pages[0]

        else:

            page = context.new_page()


        browser_restart_requested = False


        prepare_whatsapp_session(
            page
        )


        return conn, context, page


    # --------------------------------------------------------
    # Login-Zustand prüfen
    # --------------------------------------------------------

    try:

        if not is_logged_in(
            page
        ):

            logger.warning(
                "WhatsApp scheint nicht mehr angemeldet zu sein."
            )


            login_status = (
                "qr_ready"
            )


            prepare_whatsapp_session(
                page
            )


    except Exception as e:

        logger.warning(
            "WhatsApp-Session konnte nicht geprüft werden: %s",
            e,
        )


        browser_restart_requested = True


        return conn, context, page


    # --------------------------------------------------------
    # Auftrag holen
    # --------------------------------------------------------

    try:

        order = get_next_order(
            conn
        )


    except Exception as e:

        logger.error(
            "Fehler beim Lesen der Aufträge: %s",
            e,
        )


        conn = reconnect_db(
            conn
        )


        return conn, context, page


    if not order:

        time.sleep(
            WORKER_IDLE_SECONDS
        )


        return conn, context, page


    # --------------------------------------------------------
    # Auftrag bearbeiten
    # --------------------------------------------------------

    try:

        process_order(
            page,
            conn,
            order,
        )


    except Exception as e:

        logger.exception(
            "Unerwarteter Fehler bei Auftrag %s: %s",
            order["id"],
            e,
        )


        browser_error_words = [

            "target closed",

            "browser has been closed",

            "page closed",

            "context closed",

            "connection closed",

            "playwright",
        ]


        error_lower = str(
            e
        ).lower()


        browser_error = any(
            word in error_lower
            for word in browser_error_words
        )


        if browser_error:

            logger.error(
                "Browserfehler erkannt. "
                "Browser wird neu aufgebaut."
            )


            browser_restart_requested = True


        else:

            try:

                update_order(
                    conn,
                    order["id"],
                    status="fehler",
                    fehler=1,
                )


            except Exception:

                try:

                    conn.rollback()

                except Exception:
                    pass


    time.sleep(
        2
    )


    return conn, context, page


# ============================================================
# HAUPT-WORKER
# ============================================================

def worker_main():

    global browser_page
    global login_status
    global db_connected
    global shutdown_requested


    logger.info(
        
    )


    logger.info(
        "WhatsApp Worker startet"
    )


    logger.info(
        "AKTUELLE WORKER VERSION: %s",
        VERSION,
    )


    logger.info(
        "=================================================="
    )


    logger.info(
        "DRY_RUN=%s",
        DRY_RUN,
    )


    logger.info(
        "ALLOW_REAL_SEND=%s",
        ALLOW_REAL_SEND,
    )


    logger.info(
        "HEADLESS=%s",
        HEADLESS,
    )


    logger.info(
        "KEEP_BROWSER_OPEN=%s",
        KEEP_BROWSER_OPEN,
    )


    logger.info(
        "VERIFY_TIMEOUT_SECONDS=%s",
        VERIFY_TIMEOUT_SECONDS,
    )


    logger.info(
        "VERIFY_TIME_TOLERANCE_SECONDS=%s",
        VERIFY_TIME_TOLERANCE_SECONDS,
    )


    logger.info(
        "DB: %s.%s",
        DB_SCHEMA,
        DB_TABLE,
    )


    logger.info(
        "Browser-Profil: %s",
        Path(
            BROWSER_DIR
        ).absolute(),
    )


    logger.info(
        "QR-Screenshot: %s",
        QR_SCREENSHOT,
    )


    if (
        not DRY_RUN
        and ALLOW_REAL_SEND
    ):

        logger.warning(
            "=================================================="
        )


        logger.warning(
            "ECHTER VERSAND IST AKTIV."
        )


        logger.warning(
            "DRY_RUN=false"
        )


        logger.warning(
            "ALLOW_REAL_SEND=true"
        )


        logger.warning(
            "=================================================="
        )


    # --------------------------------------------------------
    # Webserver
    # --------------------------------------------------------

    web_thread = threading.Thread(
        target=start_webserver,
        daemon=True,
        name="webserver",
    )


    web_thread.start()


    # --------------------------------------------------------
    # Heartbeat
    # --------------------------------------------------------

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        daemon=True,
        name="heartbeat",
    )


    heartbeat_thread.start()


    conn = None


    try:

        # ----------------------------------------------------
        # DB initial verbinden
        # ----------------------------------------------------

        conn = get_db_connection()


        # ----------------------------------------------------
        # Playwright
        # ----------------------------------------------------

        with sync_playwright() as playwright:

            context = None

            page = None


            try:

                # --------------------------------------------
                # Browser initial starten
                # --------------------------------------------

                context = start_browser(
                    playwright
                )


                if context.pages:

                    page = context.pages[0]

                else:

                    page = context.new_page()


                browser_page = page


                # --------------------------------------------
                # WhatsApp anmelden
                # --------------------------------------------

                prepare_whatsapp_session(
                    page
                )


                # --------------------------------------------
                # Endlosschleife
                # --------------------------------------------

                while not shutdown_requested:

                    try:

                        (
                            conn,
                            context,
                            page,
                        ) = run_worker_cycle(
                            playwright,
                            conn,
                            context,
                            page,
                        )


                    except Exception as e:

                        logger.exception(
                            "Fehler im Worker-Zyklus: %s",
                            e,
                        )


                        time.sleep(
                            BROWSER_RESTART_DELAY_SECONDS
                        )


                logger.info(
                    "Worker-Schleife beendet."
                )


            finally:

                if context:

                    close_browser_context(
                        context
                    )


    except Exception as e:

        login_status = "error"


        logger.exception(
            "Kritischer Worker-Fehler: %s",
            e,
        )


        raise


    finally:

        browser_page = None

        db_connected = False


        if conn:

            try:

                conn.close()


                logger.info(
                    "Datenbankverbindung geschlossen."
                )


            except Exception:
                pass


        login_status = (
            "stopped"
        )


        logger.info(
            "WhatsApp Worker beendet."
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        worker_main()


    except KeyboardInterrupt:

        shutdown_requested = True


        logger.info(
            "Worker durch Benutzer beendet."
        )


        sys.exit(0)


    except Exception:

        logger.exception(
            "Worker wurde aufgrund eines kritischen Fehlers beendet."
        )


        sys.exit(1)

