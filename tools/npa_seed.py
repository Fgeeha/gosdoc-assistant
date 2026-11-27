"""Первичное наполнение нормативной базы из ИПС «Законодательство России» (pravo.gov.ru).

Временная мера до подключения правовой системы с API. Запускается вручную с
машины, у которой есть доступ к pravo.gov.ru, — само приложение наружу не ходит.

    uv run python tools/npa_seed.py fetch                 # скачать редакции в var/npa-seed
    uv run python tools/npa_seed.py upload                # загрузить в запущенную систему

Дата редакции — дата изменяющего акта (так же её указывают в ссылках «в ред. от …»),
а не дата вступления изменений в силу.
"""

import argparse
import csv
import html
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("npa_seed")

BASE = "http://pravo.gov.ru/proxy/ips/?"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/128.0"
ROOT = Path(__file__).resolve().parent.parent
ACTS_CSV = Path(__file__).with_name("npa_acts.csv")
OUT_DIR = ROOT / "var" / "npa-seed"

# Вид из списка → (начало названия в выдаче поиска, заголовок, который понимает система)
KINDS = {
    "fz": (("Федеральный закон", "Кодекс"), "Федеральный закон"),
    "fkz": (("Федеральный конституционный закон",), "Федеральный конституционный закон"),
    "pp": (
        ("Постановление Правительства Российской Федерации",),
        "Постановление Правительства Российской Федерации",
    ),
    "up": (("Указ Президента Российской Федерации",), "Указ Президента Российской Федерации"),
}
_EMPTY_ATTRS = ("a3", "a6", "a15", "a16", "a17", "a4", "a23")


@dataclass
class Edition:
    rdk: int
    date: str  # ISO; дата изменяющего акта, для исходной редакции — дата принятия
    by: str  # чем изменён: «от 28.12.2024 № 547-ФЗ»
    file: str = ""  # пусто — редакция не скачивалась


@dataclass
class Act:
    kind: str
    number: str
    date: str
    title: str
    nd: str
    repealed_at: str = ""
    repealed_note: str = ""
    editions: list[Edition] = field(default_factory=list)
    changes: list[tuple[str, str]] = field(default_factory=list)  # (дата, изменяющий акт)
    status: str = ""  # как в выдаче поиска: «Действует с изменениями», «Утратил силу»


def get(params: list[tuple[str, str]], pause: float) -> str:
    url = BASE + urllib.parse.urlencode(params, encoding="cp1251")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310
                time.sleep(pause)
                return str(response.read().decode("cp1251", "replace"))
        except OSError as e:
            log.warning("Повтор %d после ошибки сети: %s", attempt + 1, e)
            time.sleep(5 * (attempt + 1))
    raise OSError(f"Не удалось получить {url}")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _iso(ddmmyyyy: str) -> str:
    return datetime.strptime(ddmmyyyy, "%d.%m.%Y").date().isoformat()


def find_act(kind: str, number: str, year: str, pause: float) -> Act | None:
    """Поиск по точному номеру в пределах года принятия: номера законов повторяются по годам."""
    params = [("list_itself", ""), ("bpas", "cd00000")]
    for name in _EMPTY_ATTRS[:3]:
        params += [(name, ""), (f"{name}type", "1"), (f"{name}value", "")]
    params += [("a7type", "4"), ("a7from", f"01.01.{year}"), ("a7to", f"31.12.{year}")]
    params += [("a7date", ""), ("a8", number), ("a8type", "2"), ("a1", ""), ("a0", "")]
    for name in _EMPTY_ATTRS[3:]:
        params += [(name, ""), (f"{name}type", "1"), (f"{name}value", "")]
    params += [("textpres", ""), ("sort", "7"), ("page", "firstlast")]
    page = get(params, pause)
    prefixes = KINDS[kind][0]
    for item in page.split("<!-- BEGIN элемент списка -->")[1:]:
        link = re.search(r'nd=(\d+)[^>]*class="bold"[^>]*>(.*?)</a>', item, re.S)
        if link is None:
            continue
        name = _text(link.group(2))
        dated = re.search(r"от (\d{2}\.\d{2}\.\d{4}) № (\S+)$", name)
        if dated is None or not name.startswith(prefixes) or dated.group(2) != number:
            continue
        title = re.search(r'<span class="bold">(.*?)</span>', item, re.S)
        status = re.search(r'tiny_italic_bold">(.*?)</span>', item, re.S)
        return Act(
            kind,
            number,
            _iso(dated.group(1)),
            _text(title.group(1)) if title else "",
            link.group(1),
            status=_text(status.group(1)) if status else "",
        )
    return None


def load_editions(act: Act, pause: float) -> None:
    page = get([("docbody", ""), ("nd", act.nd)], pause)
    for _ in range(3):  # портал изредка отдаёт страницу без списка редакций
        if f",{act.nd}'" in page or "без изменений" in act.status:
            break
        time.sleep(5)
        page = get([("docbody", ""), ("nd", act.nd)], pause)
    seen: set[int] = set()
    for rdk, label in re.findall(rf"<option value='(\d+),{act.nd}'[^>]*>([^<]*)", page):
        if int(rdk) in seen:
            continue
        seen.add(int(rdk))
        changed = re.match(r"\d+ - от (\d{2}\.\d{2}\.\d{4})(?: № (\S+))?", label.strip())
        if changed is None:  # «Исходная редакция»
            act.editions.append(Edition(int(rdk), act.date, ""))
            continue
        by = f"от {changed.group(1)}" + (f" № {changed.group(2)}" if changed.group(2) else "")
        if "не действ" in label:
            # Отменяющий акт может быть принят, но ещё не вступить в силу — тогда акт
            # действует, и отметку ставить нельзя. Верим статусу из выдачи поиска.
            if "Утратил силу" in act.status:
                act.repealed_at = _iso(changed.group(1))
                act.repealed_note = f"Утратил силу: акт {by}"
            continue
        act.editions.append(Edition(int(rdk), _iso(changed.group(1)), by))
        act.changes.append((_iso(changed.group(1)), by))
    if not act.editions:  # у акта нет списка редакций — действует в исходной
        act.editions.append(Edition(0, act.date, ""))
    # Номера редакций идут в порядке вступления изменений в силу, а даты изменяющих
    # актов — нет. Система считает действующей редакцию с самой поздней датой, поэтому
    # дата редакции не может быть раньше предыдущей; на одну дату остаётся последняя.
    act.editions.sort(key=lambda e: e.rdk)
    latest = ""
    for edition in act.editions:
        latest = edition.date = max(latest, edition.date)
    act.editions = list({e.date: e for e in act.editions}.values())


def paragraphs(page: str) -> list[str]:
    body = page[page.find('id="text_content"') :]
    body = re.sub(r"<(style|xml|head|script)\b.*?</\1>", "", body, flags=re.S)
    # Надстрочная цифра в номере статьи или части: «5<sup>1</sup>» → «5.1»
    body = re.sub(r'(\d)<span class="W9"[^>]*>(\d+)</span>', r"\1.\2", body)
    found: list[str] = []
    for attrs, inner in re.findall(r"<p\b([^>]*)>(.*?)</p>", body, flags=re.S):
        if 'class="M"' not in attrs:
            found.append(_text(inner))
            continue
        # Старые акты — моноширинный текст одним блоком: абзац начинается с отступа.
        current = ""
        for line in html.unescape(re.sub(r"<[^>]+>", "", inner)).splitlines():
            if not line.strip() or line.startswith("    "):
                found.append(current)
                current = ""
            current = f"{current} {line.strip()}".strip()
        found.append(current)
    return [re.sub(r"\s+", " ", p) for p in found if p]


def save_edition(act: Act, edition: Edition, out: Path, pause: float) -> None:
    name = f"{act.kind}_{act.number}_{act.date[:4]}__{edition.date}_r{edition.rdk}.html"
    name = re.sub(r"[^\w.\-]", "-", name)
    edition.file = name
    if (out / name).exists():
        return
    params = [("doc_itself", ""), ("nd", act.nd), ("page", "1")]
    page = get([*params, ("rdk", str(edition.rdk)), ("fulltext", "1")], pause)
    body = paragraphs(page)
    if len(body) < 3:  # у утратившего силу акта текст отдаётся только без номера редакции
        body = paragraphs(get([*params, ("rdk", ""), ("fulltext", "1")], pause))
    if len(body) < 3:
        edition.file = ""
        log.warning("Пустой текст: %s № %s, редакция %d", act.kind, act.number, edition.rdk)
        return
    adopted = date.fromisoformat(act.date)
    head = [f"{KINDS[act.kind][1]} от {adopted:%d.%m.%Y} № {act.number}", act.title]
    if edition.rdk:
        head.append(f"(в ред. от {date.fromisoformat(edition.date):%d.%m.%Y})")
    lines = "\n".join(f"<p>{html.escape(p)}</p>" for p in [*head, *body] if p)
    (out / name).write_text(
        f'<html><head><meta charset="utf-8"><title>{html.escape(act.title)}</title></head>'
        f"<body>\n{lines}\n</body></html>\n",
        encoding="utf-8",
    )


def write_changes(acts: list[Act], since: str, out: Path) -> None:
    rows = sorted(
        ((when, a, by) for a in acts for when, by in a.changes if when >= since),
        key=lambda r: r[0],
        reverse=True,
    )
    lines = [
        f"# Изменения в актах с {date.fromisoformat(since):%d.%m.%Y}",
        "",
        f"Источник: ИПС «Законодательство России», собрано {date.today():%d.%m.%Y}.",
        "",
        "| Дата | Акт | Изменён актом |",
        "|---|---|---|",
    ]
    for when, act, by in rows:
        label = f"№ {act.number} от {date.fromisoformat(act.date):%d.%m.%Y} «{act.title}»"
        lines.append(f"| {date.fromisoformat(when):%d.%m.%Y} | {label} | {by} |")
    repealed = [a for a in acts if a.repealed_at]
    if repealed:
        lines += ["", "## Утратили силу", "", "| Акт | Основание |", "|---|---|"]
        lines += [f"| № {a.number} «{a.title}» | {a.repealed_note} |" for a in repealed]
    (out / "changes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def fetch(args: argparse.Namespace) -> int:
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    acts: list[Act] = []
    missing: list[str] = []
    with ACTS_CSV.open(encoding="utf-8") as f:
        rows = [r for r in csv.reader(f, delimiter=";") if r and not r[0].startswith("#")]
    for kind, number, year, *_ in rows[: args.limit]:
        try:
            act = find_act(kind, number, year, args.pause)
            if act is None:
                missing.append(f"{kind} {number} ({year})")
                log.warning("Не найден: %s № %s, %s год", kind, number, year)
                continue
            load_editions(act, args.pause)
            recent = [e for e in act.editions if e.date >= args.since]
            older = [e for e in act.editions if e.date < args.since][-1:]
            for edition in (older + recent)[-args.max_editions :]:
                save_edition(act, edition, out, args.pause)
        except OSError as e:
            missing.append(f"{kind} {number} ({year}): {e}")
            continue
        acts.append(act)
        saved = sum(bool(e.file) for e in act.editions)
        log.info("%s № %s: редакций %d, скачано %d", kind, number, len(act.editions), saved)
    manifest = {"since": args.since, "acts": [asdict(a) for a in acts], "missing": missing}
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    write_changes(acts, args.since, out)
    log.info("Актов: %d, не найдено: %d. Каталог: %s", len(acts), len(missing), out)
    return 0


def upload(args: argparse.Namespace) -> int:
    import httpx

    out: Path = args.out
    manifest: dict[str, Any] = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    password = os.environ.get("GOSDOC_PASSWORD")
    if not password:
        log.error("Задайте пароль в переменной окружения GOSDOC_PASSWORD")
        return 2
    failed = 0
    with httpx.Client(base_url=args.url, timeout=300) as api:
        credentials = {"login": args.login, "password": password}
        api.post("/api/auth/login", json=credentials).raise_for_status()
        marked = {
            a["id"]: (a["repealed_at"], a["repealed_note"])
            for a in api.get("/api/npa/acts").json()
            if a["repealed_at"]
        }
        # Повторный запуск догружает только новое. Строка — «файл<TAB>id акта»: по id
        # название и отметка об утрате силы обновляются и без новых редакций.
        done_file = out / "uploaded.txt"
        lines = done_file.read_text(encoding="utf-8").splitlines() if done_file.exists() else []
        done = {name: act_id for name, _, act_id in (line.partition("\t") for line in lines)}
        for act in manifest["acts"]:
            act_id = None
            for edition in act["editions"]:
                if edition["file"] in done:
                    act_id = act_id or int(done[edition["file"]] or 0) or None
                    continue
                if not edition["file"]:
                    continue
                data = (out / edition["file"]).read_bytes()
                response = api.post(
                    "/api/npa/upload",
                    files={"files": (edition["file"], data, "text/html")},
                    data={"shared": "true", "effective_from": edition["date"]},
                )
                response.raise_for_status()
                result = response.json()[0]
                if not result["ok"]:
                    failed += 1
                    log.warning("%s: %s", edition["file"], result["message"])
                    continue
                act_id = result["act_id"]
                with done_file.open("a", encoding="utf-8") as f:
                    f.write(f"{edition['file']}\t{act_id}\n")
            if act_id is None:
                continue
            patch = {
                "title": act["title"],
                "repealed_at": act["repealed_at"] or None,
                "repealed_note": act["repealed_note"],
            }
            if known := marked.get(act_id):  # отметку, поставленную вручную, не затираем
                patch["repealed_at"], patch["repealed_note"] = known
            api.patch(f"/api/npa/acts/{act_id}", json=patch).raise_for_status()
            log.info("Загружен: № %s «%s»", act["number"], act["title"][:60])
    log.info("Готово, ошибок: %d", failed)
    return 1 if failed else 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    sub = parser.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="скачать редакции актов из списка")
    f.add_argument("--since", default="2024-01-01", help="редакции начиная с даты (ISO)")
    f.add_argument("--max-editions", type=int, default=6, help="не больше редакций на акт")
    f.add_argument("--pause", type=float, default=0.7, help="пауза между запросами, с")
    f.add_argument("--limit", type=int, default=None, help="только первые N актов списка")
    u = sub.add_parser("upload", help="загрузить скачанное в запущенную систему")
    u.add_argument("--url", default="http://localhost:8080")
    u.add_argument("--login", default="admin")
    args = parser.parse_args()
    return fetch(args) if args.cmd == "fetch" else upload(args)


if __name__ == "__main__":
    raise SystemExit(main())
