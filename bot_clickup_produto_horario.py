import os
import requests
from datetime import datetime, timedelta
from collections import Counter
import pytz

# ===================== CONFIG A PARTIR DOS SECRETS =====================
SLACK_BOT_TOKEN   = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID  = os.getenv("SLACK_CHANNEL_ID")
CLICKUP_TOKEN     = os.getenv("CLICKUP_TOKEN")
CLICKUP_LIST_IDS  = os.getenv("CLICKUP_LIST_IDS", "205073978")
PRODUCT_FIELD_NAME = "⚫ Produto"

# Caso falte alguma variável obrigatória:
if not all([SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, CLICKUP_TOKEN]):
    raise RuntimeError("❌ Faltam variáveis de ambiente: verifique os GitHub Secrets.")

# =======================================================================

TZ = pytz.timezone("America/Fortaleza")
TIMEOUT = 30


def _ms(dt): return int(dt.timestamp() * 1000)


def ranges_ms():
    agora = datetime.now(TZ)
    inicio_dia = TZ.localize(datetime(agora.year, agora.month, agora.day, 0, 0, 0))
    uma_hora_atras = agora - timedelta(hours=1)
    ontem_inicio = inicio_dia - timedelta(days=1)
    ontem_fim = inicio_dia - timedelta(milliseconds=1)
    return {
        "hoje_ini": _ms(inicio_dia),
        "agora": _ms(agora),
        "uma_hora_atras": _ms(uma_hora_atras),
        "ontem_ini": _ms(ontem_inicio),
        "ontem_fim": _ms(ontem_fim),
    }


# --- Produto ---
def extract_product(task: dict) -> str:
    for cf in task.get("custom_fields") or []:
        if (cf.get("name") or "").strip() != PRODUCT_FIELD_NAME:
            continue
        val = cf.get("value")
        options = (cf.get("type_config") or {}).get("options") or []
        id2label = {str(o.get("id")): o.get("label") or o.get("name") for o in options if o}
        order2label = {}
        for o in options:
            if not o:
                continue
            if isinstance(o.get("orderindex"), int):
                order2label[o["orderindex"]] = o.get("label") or o.get("name")
                order2label[str(o["orderindex"])] = o.get("label") or o.get("name")

        if isinstance(val, list):
            nomes = []
            for v in val:
                nomes.append(id2label.get(str(v)) or order2label.get(v) or str(v))
            return " / ".join([n for n in nomes if n]) or "Sem produto"
        if val is not None:
            return id2label.get(str(val)) or order2label.get(val) or str(val)
        return "Sem produto"
    return "Sem produto"


# --- ClickUp API ---
def fetch_tasks_from_list(list_id: str, ini_ms: int, fim_ms: int):
    url = f"https://api.clickup.com/api/v2/list/{list_id}/task"
    headers = {"Authorization": CLICKUP_TOKEN}
    params = {
        "include_closed": "true",
        "subtasks": "true",
        "page": 0,
        "date_created_gt": ini_ms,
        "date_created_lt": fim_ms
    }
    todas = []
    while True:
        r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        tasks = data.get("tasks", [])
        if not tasks:
            break
        todas.extend(tasks)
        params["page"] += 1
    return todas


def fetch_tasks_range(ini_ms: int, fim_ms: int):
    all_tasks = []
    for lid in [x.strip() for x in CLICKUP_LIST_IDS.split(",") if x.strip()]:
        all_tasks.extend(fetch_tasks_from_list(lid, ini_ms, fim_ms))
    return all_tasks


# --- Contagens ---
def count_by_product(tasks, mode="created", ini_ms=None, fim_ms=None):
    c = Counter()
    for t in tasks:
        prod = extract_product(t)
        if mode == "closed":
            closed = t.get("date_closed")
            if not closed:
                continue
            closed = int(closed)
            if ini_ms and closed < ini_ms:
                continue
            if fim_ms and closed > fim_ms:
                continue
        c[prod] += 1
    return Counter(dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0] or ""))))


# --- Tabela ---
def make_table(counter_yesterday, counter_day, counter_hour, counter_closed_today):
    header_prod = "Produto"
    headers = ["Ontem", "Hoje", "Hora", "Fechados"]

    produtos = sorted(
        set(counter_hour.keys())
        | set(counter_day.keys())
        | set(counter_yesterday.keys())
        | set(counter_closed_today.keys())
    )

    # Ordena pelo valor de Hoje (desc)
    produtos = sorted(produtos, key=lambda p: (-counter_day.get(p, 0), p or ""))

    # Calcula larguras automáticas
    col1 = max(len(header_prod), *(len(p or "Sem produto") for p in produtos)) if produtos else len(header_prod)
    col2 = max(len("Ontem"), *(len(str(counter_yesterday.get(p, 0))) for p in produtos)) + 1
    col3 = max(len("Hoje"), *(len(str(counter_day.get(p, 0))) for p in produtos)) + 1
    col4 = max(len("Hora"), *(len(str(counter_hour.get(p, 0))) for p in produtos)) + 1
    col5 = max(len("Fechados"), *(len(str(counter_closed_today.get(p, 0))) for p in produtos)) + 1

    header_line = f"{header_prod:<{col1}} {headers[0]:>{col2}} {headers[1]:>{col3}} {headers[2]:>{col4}} {headers[3]:>{col5}}"
    sep_line = f"{'-'*col1} {'-'*col2} {'-'*col3} {'-'*col4} {'-'*col5}"

    linhas = [header_line, sep_line]

    for p in produtos:
        v_yes = counter_yesterday.get(p, 0)
        v_day = counter_day.get(p, 0)
        v_hr = counter_hour.get(p, 0)
        v_cls = counter_closed_today.get(p, 0)
        nome = p or "Sem produto"
        linhas.append(
            f"{nome:<{col1}} {v_yes:>{col2}} {v_day:>{col3}} {v_hr:>{col4}} {v_cls:>{col5}}"
        )

    return "```\n" + "\n".join(linhas) + "\n```"


# --- Slack ---
def post_to_slack(counter_yesterday, counter_day, counter_hour, counter_closed_today):
    total_yest = sum(counter_yesterday.values())
    total_day = sum(counter_day.values())
    total_hour = sum(counter_hour.values())
    total_closed = sum(counter_closed_today.values())
    hora_str = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    tabela = make_table(counter_yesterday, counter_day, counter_hour, counter_closed_today)

    resumo = f"📅 Ontem: {total_yest}  |  📅 Hoje: {total_day}  |  🕐 Hora: {total_hour}  |  ✅ Fechados: {total_closed}"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "📊 Tasks por Produto"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"*{hora_str}* (America/Fortaleza)"}]},
        {"type": "section", "text": {"type": "mrkdwn", "text": resumo}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": tabela}},
    ]

    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {"channel": SLACK_CHANNEL_ID, "blocks": blocks, "text": resumo}
    r = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Erro Slack: {data}")


def main():
    rng = ranges_ms()

    # --- RESTRIÇÃO DE HORÁRIO ---
    hora_atual = datetime.now(TZ).hour
    if hora_atual < 8 or hora_atual > 20:
        print(f"⏰ {datetime.now(TZ).strftime('%H:%M')} - Fora do horário de envio (08h–20h).")
        return
    # -----------------------------------------------------------------

    # Hoje (criadas)
    tasks_day = fetch_tasks_range(rng["hoje_ini"], rng["agora"])
    counter_day = count_by_product(tasks_day, mode="created")

    # Última hora
    tasks_hour = [t for t in tasks_day if rng["uma_hora_atras"] <= int(t.get("date_created", 0)) <= rng["agora"]]
    counter_hour = count_by_product(tasks_hour, mode="created")

    # Ontem
    tasks_yest = fetch_tasks_range(rng["ontem_ini"], rng["ontem_fim"])
    counter_yest = count_by_product(tasks_yest, mode="created")

    # Fechadas hoje
    tasks_all = fetch_tasks_range(rng["ontem_ini"], rng["agora"])
    counter_closed_today = count_by_product(tasks_all, mode="closed", ini_ms=rng["hoje_ini"], fim_ms=rng["agora"])

    post_to_slack(counter_yest, counter_day, counter_hour, counter_closed_today)
    print(f"✅ Mensagem enviada ao Slack às {datetime.now(TZ).strftime('%H:%M')}.")


if __name__ == "__main__":
    main()
