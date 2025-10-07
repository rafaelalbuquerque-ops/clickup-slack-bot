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

if not all([SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, CLICKUP_TOKEN]):
    raise RuntimeError("❌ Faltam variáveis de ambiente: verifique os GitHub Secrets.")

# =======================================================================

TZ = pytz.timezone("America/Sao_Paulo")
TIMEOUT = 30


def _ms(dt): 
    return int(dt.timestamp() * 1000)


def ranges_ms():
    agora = datetime.now(TZ)
    inicio_mes = TZ.localize(datetime(agora.year, agora.month, 1, 0, 0, 0))
    inicio_dia = TZ.localize(datetime(agora.year, agora.month, agora.day, 0, 0, 0))
    ontem_inicio = inicio_dia - timedelta(days=1)
    ontem_fim = inicio_dia - timedelta(milliseconds=1)
    return {
        "mes_ini": _ms(inicio_mes),
        "hoje_ini": _ms(inicio_dia),
        "ontem_ini": _ms(ontem_inicio),
        "ontem_fim": _ms(ontem_fim),
        "agora": _ms(agora),
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
            nomes = [id2label.get(str(v)) or order2label.get(v) or str(v) for v in val]
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
    return c


# --- Tabela ---
def make_table(counter_month, counter_yesterday, counter_today, counter_closed_month, counter_closed_today):
    header_prod = "Produto"
    headers = ["Mês", "Ontem", "Hoje", "Fech. Mês", "Fech. Hj"]

    produtos = sorted(
        set(counter_month.keys())
        | set(counter_today.keys())
        | set(counter_yesterday.keys())
        | set(counter_closed_month.keys())
        | set(counter_closed_today.keys())
    )

    produtos = sorted(
        produtos,
        key=lambda p: (
            -counter_month.get(p, 0),
            -counter_today.get(p, 0),
            -counter_closed_month.get(p, 0),
            p or ""
        )
    )

    # Larguras automáticas ajustadas
    col1 = max(len(header_prod), *(len(p or "Sem produto") for p in produtos)) if produtos else len(header_prod)
    col2 = max(len(headers[0]), *(len(str(counter_month.get(p, 0))) for p in produtos)) + 2
    col3 = max(len(headers[1]), *(len(str(counter_yesterday.get(p, 0))) for p in produtos)) + 2
    col4 = max(len(headers[2]), *(len(str(counter_today.get(p, 0))) for p in produtos)) + 2
    col5 = max(len(headers[3]), *(len(str(counter_closed_month.get(p, 0))) for p in produtos)) + 2
    col6 = max(len(headers[4]), *(len(str(counter_closed_today.get(p, 0))) for p in produtos)) + 2

    header_line = (
        f"{header_prod:<{col1}} "
        f"{headers[0]:>{col2}} {headers[1]:>{col3}} {headers[2]:>{col4}} "
        f"{headers[3]:>{col5}} {headers[4]:>{col6}}"
    )
    sep_line = f"{'-'*col1} {'-'*col2} {'-'*col3} {'-'*col4} {'-'*col5} {'-'*col6}"

    linhas = [header_line, sep_line]

    for p in produtos:
        nome = p or "Sem produto"
        v_mes = counter_month.get(p, 0)
        v_yes = counter_yesterday.get(p, 0)
        v_day = counter_today.get(p, 0)
        v_cls_mes = counter_closed_month.get(p, 0)
        v_cls_day = counter_closed_today.get(p, 0)
        linhas.append(
            f"{nome:<{col1}} "
            f"{v_mes:>{col2}} {v_yes:>{col3}} {v_day:>{col4}} "
            f"{v_cls_mes:>{col5}} {v_cls_day:>{col6}}"
        )

    return "```\n" + "\n".join(linhas) + "\n```"


# --- Slack ---
def post_to_slack(counter_month, counter_yesterday, counter_today, counter_closed_month, counter_closed_today):
    total_month = sum(counter_month.values())
    total_yest = sum(counter_yesterday.values())
    total_today = sum(counter_today.values())
    total_closed_month = sum(counter_closed_month.values())
    total_closed_today = sum(counter_closed_today.values())

    hora_str = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    tabela = make_table(counter_month, counter_yesterday, counter_today, counter_closed_month, counter_closed_today)

    resumo = (
        f"📅 Aber. Mês: {total_month}  |  📅 Ontem: {total_yest}  |  📅 Hoje: {total_today}  "
        f"|  ✅ Fech. Mês: {total_closed_month}  |  ✅ Fech. Hj: {total_closed_today}"
    )

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "📊 Tasks Abertas"}},
      #  {"type": "context", "elements": [{"type": "mrkdwn", "text": f"*{hora_str}* (America/Sao_Paulo)"}]},
        {"type": "section", "text": {"type": "mrkdwn", "text": resumo}},
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


# --- MAIN ---
def main():
    rng = ranges_ms()

    hora_atual = datetime.now(TZ).hour
    if hora_atual < 8 or hora_atual > 20:
        print(f"⏰ {datetime.now(TZ).strftime('%H:%M')} - Fora do horário de envio (08h–20h).")
        return

    tasks_month = fetch_tasks_range(rng["mes_ini"], rng["agora"])
    counter_month = count_by_product(tasks_month, mode="created")

    tasks_yest = fetch_tasks_range(rng["ontem_ini"], rng["ontem_fim"])
    counter_yest = count_by_product(tasks_yest, mode="created")

    tasks_today = fetch_tasks_range(rng["hoje_ini"], rng["agora"])
    counter_today = count_by_product(tasks_today, mode="created")

    tasks_all = fetch_tasks_range(rng["mes_ini"], rng["agora"])
    counter_closed_month = count_by_product(tasks_all, mode="closed", ini_ms=rng["mes_ini"], fim_ms=rng["agora"])

    tasks_recent = fetch_tasks_range(rng["ontem_ini"], rng["agora"])
    counter_closed_today = count_by_product(tasks_recent, mode="closed", ini_ms=rng["hoje_ini"], fim_ms=rng["agora"])

    post_to_slack(counter_month, counter_yest, counter_today, counter_closed_month, counter_closed_today)
    print(f"✅ Mensagem enviada ao Slack às {datetime.now(TZ).strftime('%H:%M')}.")


if __name__ == "__main__":
    main()

