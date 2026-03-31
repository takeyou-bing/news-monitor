"""
네이버 뉴스 → 텔레그램 모니터링 봇 (Render 안정화 버전 v2)
pip install streamlit requests
"""

import re
import json
import time
import hashlib
import threading
import requests
import streamlit as st
from datetime import datetime
from pathlib import Path

import urllib3
urllib3.disable_warnings()

# ─────────────────────────────────────────
# 파일 경로
# ─────────────────────────────────────────
CONFIG_FILE  = Path("config.json")
CACHE_FILE   = Path("sent_articles.json")
LOG_FILE     = Path("monitor.log")
THREAD_FLAG  = Path("thread.lock")  # 스레드 중복 방지 플래그

DEFAULT_CONFIG = {
    "naver_client_id":     "",
    "naver_client_secret": "",
    "telegram_bot_token":  "",
    "telegram_chat_id":    "",
    "keywords":            [],
    "display_count":       10,
    "interval_minutes":    10,
    "sort":                "date",
    "running":             False,
}

# ─────────────────────────────────────────
# 설정 / 캐시 / 로그
# ─────────────────────────────────────────
def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def load_cache():
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_cache(sent):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sent)[-5000:], f, ensure_ascii=False)

def write_log(lines):
    existing = LOG_FILE.read_text(encoding="utf-8").splitlines() if LOG_FILE.exists() else []
    all_lines = existing + lines
    LOG_FILE.write_text("\n".join(all_lines[-200:]), encoding="utf-8")

def article_id(link):
    return hashlib.md5(link.encode()).hexdigest()

# ─────────────────────────────────────────
# 네이버 / 텔레그램
# ─────────────────────────────────────────
def clean_html(text):
    return re.sub(r"<[^>]+>", "", text).replace("&amp;","&").replace("&lt;","<").replace("&gt;",">").replace("&quot;",'"')

def search_naver(keyword, cfg):
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id":     cfg["naver_client_id"],
        "X-Naver-Client-Secret": cfg["naver_client_secret"],
    }
    params = {"query": keyword, "display": cfg["display_count"], "start": 1, "sort": cfg["sort"]}
    res = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
    res.raise_for_status()
    return res.json().get("items", [])

def send_telegram(message, cfg):
    url = f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/sendMessage"
    payload = {"chat_id": cfg["telegram_chat_id"], "text": message,
               "parse_mode": "HTML", "disable_web_page_preview": False}
    res = requests.post(url, json=payload, timeout=15, verify=False)
    res.raise_for_status()

def format_message(keyword, article):
    title       = clean_html(article.get("title", ""))
    description = clean_html(article.get("description", ""))
    link        = article.get("link", "")
    pub_date    = article.get("pubDate", "")
    try:
        dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
        date_str = dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        date_str = pub_date
    return (
        f"🔔 <b>[{keyword}]</b> 새 뉴스\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📰 <b>{title}</b>\n\n"
        f"{description[:150]}{'...' if len(description) > 150 else ''}\n\n"
        f"🕐 {date_str}\n🔗 {link}"
    )

# ─────────────────────────────────────────
# 백그라운드 모니터링 (단일 스레드 보장)
# ─────────────────────────────────────────
def _monitor_loop():
    """단 하나의 스레드만 실행되도록 파일 플래그로 제어"""
    # 이미 다른 스레드가 실행 중이면 즉시 종료
    if THREAD_FLAG.exists():
        return
    THREAD_FLAG.write_text("running")

    try:
        while True:
            try:
                cfg = load_config()
                if not cfg.get("running"):
                    time.sleep(10)
                    continue

                sent  = load_cache()
                new_n = 0
                ts    = datetime.now().strftime("%H:%M:%S")
                lines = [f"[{ts}] === 검색 시작 ==="]

                for kw in cfg.get("keywords", []):
                    try:
                        articles = search_naver(kw, cfg)
                        lines.append(f"[{ts}] [{kw}] {len(articles)}건 검색")
                        for art in articles:
                            aid = article_id(art.get("link", ""))
                            if aid in sent:
                                continue
                            try:
                                send_telegram(format_message(kw, art), cfg)
                                sent.add(aid)
                                new_n += 1
                                lines.append(f"[{ts}]   ✅ {clean_html(art.get('title',''))[:40]}")
                                time.sleep(1)  # 텔레그램 rate limit 방지
                            except Exception as e:
                                lines.append(f"[{ts}]   ❌ 전송 오류: {e}")
                                time.sleep(2)
                    except Exception as e:
                        lines.append(f"[{ts}] [{kw}] 검색 오류: {e}")
                    time.sleep(1)

                save_cache(sent)
                lines.append(f"[{ts}] === 완료: 새 기사 {new_n}건 ===")
                write_log(lines)

                # 다음 검색까지 대기 (1초씩 체크)
                interval = load_config().get("interval_minutes", 10) * 60
                for _ in range(interval):
                    if not load_config().get("running"):
                        break
                    time.sleep(1)

            except Exception as e:
                write_log([f"[오류] {e}"])
                time.sleep(30)
    finally:
        # 스레드 종료 시 플래그 삭제
        if THREAD_FLAG.exists():
            THREAD_FLAG.unlink()

def start_monitor():
    """스레드가 없을 때만 새로 시작"""
    if not THREAD_FLAG.exists():
        t = threading.Thread(target=_monitor_loop, daemon=True)
        t.start()

# 앱 시작 시 실행 (플래그 없을 때만 스레드 생성)
start_monitor()

# ─────────────────────────────────────────
# Streamlit UI
# ─────────────────────────────────────────
st.set_page_config(page_title="뉴스 모니터링 봇", page_icon="📰", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #0f1117; }
    .block-container { padding-top: 1.5rem; }
    .status-running { color: #2ecc71; font-weight: bold; font-size: 1.1rem; }
    .status-stopped { color: #e74c3c; font-weight: bold; font-size: 1.1rem; }
    .log-box { background: #0d1117; color: #58a6ff; font-family: monospace;
               font-size: 0.8rem; padding: 12px; border-radius: 8px;
               height: 320px; overflow-y: auto; white-space: pre-wrap; }
</style>
""", unsafe_allow_html=True)

cfg = load_config()

col_title, col_status = st.columns([3, 1])
with col_title:
    st.markdown("## 📰 뉴스 모니터링 봇")
with col_status:
    if cfg.get("running"):
        st.markdown('<p class="status-running">🟢 실행 중</p>', unsafe_allow_html=True)
    else:
        st.markdown('<p class="status-stopped">⏹ 정지됨</p>', unsafe_allow_html=True)

st.divider()
tab1, tab2, tab3 = st.tabs(["⚙ 설정", "🔑 API 키", "📋 로그"])

# ── 설정 탭 ──
with tab1:
    st.subheader("검색 설정")
    col1, col2, col3 = st.columns(3)
    with col1:
        display_count = st.number_input("검색 건수 (최대 100)", min_value=1, max_value=100, value=cfg["display_count"])
    with col2:
        interval = st.number_input("검색 주기 (분)", min_value=1, max_value=1440, value=cfg["interval_minutes"])
    with col3:
        sort_opt = st.selectbox("정렬 방식", ["date (최신순)", "sim (정확도순)"],
                                index=0 if cfg["sort"] == "date" else 1)

    st.divider()
    st.subheader("키워드 관리")
    kw_col1, kw_col2 = st.columns([4, 1])
    with kw_col1:
        new_kw = st.text_input("키워드 입력", placeholder="예: 빙그레 -광고  /  삼성전자 실적",
                                label_visibility="collapsed")
    with kw_col2:
        if st.button("➕ 추가", use_container_width=True):
            if new_kw.strip():
                if new_kw.strip() not in cfg["keywords"]:
                    cfg["keywords"].append(new_kw.strip())
                    save_config(cfg)
                    st.success(f"'{new_kw.strip()}' 추가됨")
                    st.rerun()
                else:
                    st.warning("이미 등록된 키워드예요.")

    if cfg["keywords"]:
        st.markdown("**등록된 키워드**")
        for i, kw in enumerate(cfg["keywords"]):
            k1, k2 = st.columns([5, 1])
            with k1:
                st.markdown(f"` {kw} `")
            with k2:
                if st.button("삭제", key=f"del_{i}"):
                    cfg["keywords"].pop(i)
                    save_config(cfg)
                    st.rerun()
    else:
        st.info("등록된 키워드가 없어요. 위에서 추가해 주세요.")

    st.divider()
    btn1, btn2, _ = st.columns([1, 1, 3])
    with btn1:
        if st.button("💾 설정 저장", use_container_width=True):
            cfg["display_count"]    = display_count
            cfg["interval_minutes"] = interval
            cfg["sort"]             = "date" if "date" in sort_opt else "sim"
            save_config(cfg)
            st.success("저장됐어요!")
    with btn2:
        if not cfg.get("running"):
            if st.button("▶ 모니터링 시작", use_container_width=True, type="primary"):
                if not cfg["naver_client_id"] or not cfg["telegram_bot_token"]:
                    st.error("API 키 탭에서 네이버/텔레그램 키를 먼저 입력해주세요.")
                elif not cfg["keywords"]:
                    st.error("키워드를 최소 1개 이상 등록해주세요.")
                else:
                    cfg["display_count"]    = display_count
                    cfg["interval_minutes"] = interval
                    cfg["sort"]             = "date" if "date" in sort_opt else "sim"
                    cfg["running"]          = True
                    save_config(cfg)
                    start_monitor()
                    st.rerun()
        else:
            if st.button("⏹ 모니터링 중지", use_container_width=True):
                cfg["running"] = False
                save_config(cfg)
                st.rerun()

# ── API 키 탭 ──
with tab2:
    st.subheader("네이버 API")
    naver_id     = st.text_input("Client ID",     value=cfg["naver_client_id"])
    naver_secret = st.text_input("Client Secret", value=cfg["naver_client_secret"], type="password")
    st.subheader("텔레그램")
    tg_token = st.text_input("Bot Token", value=cfg["telegram_bot_token"], type="password")
    tg_chat  = st.text_input("Chat ID",   value=cfg["telegram_chat_id"],
                              help="개인: 양수 / 그룹: -로 시작하는 음수")

    col_save, col_test, _ = st.columns([1, 2, 3])
    with col_save:
        if st.button("💾 저장", use_container_width=True):
            cfg["naver_client_id"]     = naver_id.strip()
            cfg["naver_client_secret"] = naver_secret.strip()
            cfg["telegram_bot_token"]  = tg_token.strip()
            cfg["telegram_chat_id"]    = tg_chat.strip()
            save_config(cfg)
            st.success("저장됐어요!")
    with col_test:
        if st.button("🔔 텔레그램 테스트 전송", use_container_width=True):
            test_cfg = {**cfg,
                        "naver_client_id":     naver_id.strip(),
                        "naver_client_secret": naver_secret.strip(),
                        "telegram_bot_token":  tg_token.strip(),
                        "telegram_chat_id":    tg_chat.strip()}
            try:
                send_telegram("✅ 테스트 메시지입니다. 뉴스봇 연결 성공!", test_cfg)
                st.success("텔레그램 테스트 전송 성공!")
            except Exception as e:
                st.error(f"전송 실패: {e}")

# ── 로그 탭 ──
with tab3:
    col_refresh, col_clear, _ = st.columns([1, 1, 4])
    with col_refresh:
        if st.button("🔄 새로고침", use_container_width=True):
            st.rerun()
    with col_clear:
        if st.button("🗑 로그 지우기", use_container_width=True):
            LOG_FILE.write_text("", encoding="utf-8")
            st.rerun()

    log_text = LOG_FILE.read_text(encoding="utf-8") if LOG_FILE.exists() else "로그가 없어요. 모니터링을 시작해 주세요."
    st.markdown(f'<div class="log-box">{log_text}</div>', unsafe_allow_html=True)
    if cfg.get("running"):
        st.caption("⏱ 로그를 보려면 새로고침 버튼을 눌러주세요.")
