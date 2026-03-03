import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from html import escape
from urllib.parse import urlencode, quote_plus
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import streamlit as st
from supabase import Client, create_client


st.set_page_config(page_title="출석부", page_icon="✅", layout="wide")
st.markdown(
    """
<style>
/* Mobile-friendly base spacing */
@media (max-width: 768px) {
  .block-container {
    padding-top: 1rem;
    padding-left: 0.8rem;
    padding-right: 0.8rem;
    padding-bottom: 1rem;
  }
  h1 {
    font-size: 1.8rem !important;
  }
  h2, h3 {
    font-size: 1.2rem !important;
  }
  p, label, [data-testid="stMetricLabel"] {
    font-size: 0.9rem !important;
  }
  [data-testid="stMetricValue"] {
    font-size: 1.8rem !important;
  }
}
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_ANON_KEY")

    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_ANON_KEY가 없습니다. .streamlit/secrets.toml 또는 환경변수를 설정하세요."
        )

    return create_client(url, key)


def fetch_all_roster(client: Client):
    result = (
        client.table("v_attendance_roster")
        .select(
            "attendance_date, level, grade, class_no, student_id, student_name, status, note, marked_by"
        )
        .order("attendance_date", desc=True)
        .order("level")
        .order("grade")
        .order("class_no")
        .order("student_name")
        .execute()
    )
    return result.data or []


def fetch_roster_by_date(client: Client, attendance_date: date):
    result = (
        client.table("v_attendance_roster")
        .select(
            "attendance_date, level, grade, class_no, student_id, student_name, status, note, marked_by"
        )
        .eq("attendance_date", attendance_date.isoformat())
        .order("level")
        .order("grade")
        .order("class_no")
        .order("student_name")
        .execute()
    )
    return result.data or []


def fetch_roster_by_range(client: Client, start_date: date, end_date: date):
    result = (
        client.table("v_attendance_roster")
        .select(
            "attendance_date, level, grade, class_no, student_id, student_name, status, note, marked_by"
        )
        .gte("attendance_date", start_date.isoformat())
        .lte("attendance_date", end_date.isoformat())
        .order("attendance_date")
        .order("level")
        .order("grade")
        .order("class_no")
        .order("student_name")
        .execute()
    )
    return result.data or []


def fetch_class_detail(client: Client):
    result = (
        client.table("v_class_detail")
        .select("level, grade, class_no, student_id, student_name")
        .order("level")
        .order("grade")
        .order("class_no")
        .order("student_name")
        .execute()
    )
    rows = result.data or []
    return [r for r in rows if r.get("student_id") and r.get("student_name")]


def fetch_class_summary(client: Client):
    result = (
        client.table("v_class_summary")
        .select("level, grade, class_no, homeroom_teacher, assistant_teachers")
        .order("level")
        .order("grade")
        .order("class_no")
        .execute()
    )
    return result.data or []


def save_attendance(
    client: Client,
    attendance_date: date,
    student_id: str,
    school_class_id: str,
    status: str,
    note: str,
) -> None:
    # 결석은 DB에 저장하지 않고, 미기록을 결석으로 간주한다.
    if normalize_status(status) != "present":
        return

    payload = {
        "attendance_date": attendance_date.isoformat(),
        "student_id": student_id,
        "school_class_id": school_class_id,
        "status": "present",
        "note": note.strip() if note else None,
    }

    try:
        client.table("attendance").upsert(payload, on_conflict="attendance_date,student_id").execute()
        return
    except Exception:
        pass

    existing = (
        client.table("attendance")
        .select("id")
        .eq("attendance_date", attendance_date.isoformat())
        .eq("student_id", student_id)
        .limit(1)
        .execute()
    ).data or []

    if existing:
        client.table("attendance").update(payload).eq("id", existing[0]["id"]).execute()
    else:
        client.table("attendance").insert(payload).execute()


def format_status_symbol(status: str) -> str:
    return "○" if status == "present" else "×"


def normalize_status(status: str) -> str:
    return "present" if status == "present" else "absent"


def day_code_from_date(value: date) -> str:
    if value.weekday() == 5:
        return "sat"
    if value.weekday() == 6:
        return "sun"
    return "other"


def day_label_from_date(value: date) -> str:
    code = day_code_from_date(value)
    if code == "sat":
        return "토요일"
    if code == "sun":
        return "일요일"
    return "평일"


def sibling_group(level: str, grade: int, class_no: int) -> str:
    if level == "middle":
        if grade == 1:
            if 1 <= class_no <= 2:
                return "형제"
            if 3 <= class_no <= 4:
                return "자매"
        elif grade == 2:
            if 1 <= class_no <= 3:
                return "형제"
            if 4 <= class_no <= 5:
                return "자매"
        elif grade == 3:
            if 1 <= class_no <= 3:
                return "형제"
            if 4 <= class_no <= 5:
                return "자매"
    elif level == "high":
        if grade == 1:
            if 1 <= class_no <= 2:
                return "형제"
            if 3 <= class_no <= 5:
                return "자매"
        elif grade == 2:
            if 1 <= class_no <= 2:
                return "형제"
            if 3 <= class_no <= 5:
                return "자매"
        elif grade == 3:
            if 1 <= class_no <= 2:
                return "형제"
            if 3 <= class_no <= 4:
                return "자매"
    return ""


def week_label_from_sunday(week_start: date) -> str:
    month_first = week_start.replace(day=1)
    days_to_sunday = (6 - month_first.weekday()) % 7
    first_sunday = month_first + timedelta(days=days_to_sunday)
    if week_start < first_sunday:
        week_no = 1
    else:
        week_no = ((week_start - first_sunday).days // 7) + 1
    week_no = min(max(week_no, 1), 5)
    return f"{week_start.year}-{week_start.month:02d} {week_no}주차"


def normalize_assistant_teacher(raw_value) -> str:
    if not raw_value:
        return "-"
    if isinstance(raw_value, list):
        names = [str(v).strip() for v in raw_value if str(v).strip()]
    else:
        text = str(raw_value).replace("/", ",").replace("|", ",").replace("\n", ",")
        names = [n.strip() for n in text.split(",") if n.strip()]
    deduped = []
    seen = set()
    for name in names:
        if name not in seen:
            deduped.append(name)
            seen.add(name)
    return deduped[0] if deduped else "-"


def get_query_value(name: str) -> str:
    value = st.query_params.get(name)
    if value is None:
        return ""
    if isinstance(value, list):
        return value[0] if value else ""
    return str(value)


def get_app_timezone() -> ZoneInfo:
    tz_name = st.secrets.get("APP_TIMEZONE") or os.getenv("APP_TIMEZONE", "Asia/Seoul")
    try:
        return ZoneInfo(str(tz_name))
    except Exception:
        return ZoneInfo("Asia/Seoul")


def current_qr_slot(tz: ZoneInfo) -> str:
    # Hourly rotating slot: YYYYMMDDHH (local app timezone)
    return datetime.now(tz).strftime("%Y%m%d%H")


def fetch_students_by_name(client: Client, student_name: str):
    if not student_name.strip():
        return []
    rows = (
        client.table("student")
        .select("id,name,source_key")
        .eq("name", student_name.strip())
        .order("source_key")
        .execute()
    ).data or []
    return rows


def find_school_class_id_by_student_id(client: Client, student_id: str) -> str:
    links = (
        client.table("student_class")
        .select("school_class_id")
        .eq("student_id", student_id)
        .limit(1)
        .execute()
    ).data or []
    if not links:
        return ""
    return links[0].get("school_class_id", "")


def handle_qr_checkin(supabase: Client):
    qr_date = get_query_value("qr_date").strip()
    qr_status = "present"
    qr_slot = get_query_value("qr_slot").strip()
    qr_source = get_query_value("source").strip()

    if qr_source != "qr":
        return False
    if not qr_date or not qr_slot:
        st.error("QR 링크 파라미터가 누락되었습니다.")
        return True

    tz = get_app_timezone()
    current_slot = current_qr_slot(tz)
    if qr_slot != current_slot:
        st.error("만료된 QR 코드입니다. 새 QR 코드로 다시 시도하세요.")
        return True

    try:
        attendance_date = date.fromisoformat(qr_date)
    except ValueError:
        st.error("QR 링크의 날짜 형식이 올바르지 않습니다.")
        return True

    if day_code_from_date(attendance_date) not in {"sat", "sun"}:
        st.error("QR 출석은 토요일/일요일만 가능합니다.")
        return True

    st.title("QR 출석 체크인")
    st.caption(f"{attendance_date} / {day_label_from_date(attendance_date)}")
    student_name_input = st.text_input("이름을 입력하세요", placeholder="예: 강한")
    candidates = fetch_students_by_name(supabase, student_name_input)
    if student_name_input.strip() and not candidates:
        st.warning("이름과 일치하는 학생을 찾지 못했습니다.")
    selected_candidate = None
    if candidates:
        labels = [
            f"{c['name']} ({c.get('source_key') or 'source_key 없음'})"
            for c in candidates
        ]
        selected_label = st.selectbox("학생 선택 (source_key 매핑)", labels, key="qr_student_pick")
        selected_candidate = candidates[labels.index(selected_label)]
    submit = st.button("출석하기")

    if submit:
        if not student_name_input.strip():
            st.warning("이름을 입력하세요.")
            return True
        if not selected_candidate:
            st.warning("학생을 선택하세요.")
            return True
        try:
            student_id = selected_candidate["id"]
            school_class_id = find_school_class_id_by_student_id(supabase, student_id)
            if not school_class_id:
                st.error("student_class에서 반 정보를 찾지 못했습니다.")
                return True

            save_attendance(
                client=supabase,
                attendance_date=attendance_date,
                student_id=student_id,
                school_class_id=school_class_id,
                status=qr_status,
                note=f"QR check-in (source_key={selected_candidate.get('source_key')})",
            )
            st.success(
                f"출석 완료: {selected_candidate['name']} "
                f"({selected_candidate.get('source_key') or 'source_key 없음'})"
            )
        except Exception as e:
            st.error(f"QR 출석 처리 실패: {e}")

    return True


def build_qr_checkin_url(base_url: str, attendance_date: date, status: str, qr_slot: str) -> str:
    params = urlencode(
        {
            "source": "qr",
            "qr_date": attendance_date.isoformat(),
            "qr_status": "present",
            "qr_slot": qr_slot,
        }
    )
    if base_url:
        return f"{base_url.rstrip('/')}/?{params}"
    return f"?{params}"


def resolve_app_base_url() -> str:
    secret_url = st.secrets.get("APP_BASE_URL") or os.getenv("APP_BASE_URL", "")
    if secret_url:
        return str(secret_url).strip()

    ctx = getattr(st, "context", None)
    headers = getattr(ctx, "headers", None) if ctx else None
    if headers:
        host = headers.get("Host") or headers.get("host")
        proto = headers.get("X-Forwarded-Proto") or headers.get("x-forwarded-proto") or "https"
        if host:
            return f"{proto}://{host}"
    return ""


def build_weekend_status_bar_chart(
    sat_present: int, sat_absent: int, sun_present: int, sun_absent: int
):
    days = ["토요일", "일요일"]
    present_vals = [sat_present, sun_present]
    absent_vals = [sat_absent, sun_absent]
    max_count = max(present_vals + absent_vals + [1])

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            name="present",
            x=days,
            y=present_vals,
            marker_color="#0ea5e9",
            text=present_vals,
            textposition="outside",
            cliponaxis=False,
        )
    )
    fig.add_trace(
        go.Bar(
            name="absent",
            x=days,
            y=absent_vals,
            marker_color="#ef4444",
            text=absent_vals,
            textposition="outside",
            cliponaxis=False,
        )
    )
    fig.update_layout(
        barmode="group",
        yaxis=dict(title="인원(명)", range=[0, max_count * 1.35]),
        xaxis=dict(title="요일"),
        margin=dict(l=20, r=20, t=20, b=20),
        legend=dict(title="상태"),
        template="plotly_dark",
        height=320,
    )
    return fig


def render_class_board(
    level_name: str,
    class_keys,
    students_by_class,
    status_by_student_day,
    homeroom_map,
    assistant_map,
):
    if not class_keys:
        return

    max_students = max(len(students_by_class.get(c, [])) for c in class_keys)

    html = []
    html.append("<style>.board-wrap{width:100%;overflow-x:auto;padding-bottom:6px}")
    html.append(".board{border-collapse:collapse;width:max-content;min-width:100%;font-size:12px;table-layout:auto}")
    html.append(".board th,.board td{border:1px solid #444;padding:3px 4px;text-align:center;white-space:nowrap}")
    html.append(".board th{background:#1f2937;color:#fff}")
    html.append(".board .left{background:#111827;color:#fff;min-width:48px}")
    html.append(".board .name{background:#0f172a;color:#e5e7eb;text-align:left;font-size:11px}")
    html.append(".board .mark{font-weight:700;min-width:30px}")
    html.append(".board .mark-present{background:#0ea5e9;color:#001018}")
    html.append(".board .mark-absent{background:#ef4444;color:#ffffff}")
    html.append(".board .empty{background:#1f2937;color:#6b7280}")
    html.append("@media (max-width: 768px){.board{font-size:11px}.board th,.board td{padding:2px 3px}.board .name{font-size:10px}}")
    html.append("</style>")

    html.append(f"<h4>{escape(level_name)}</h4>")
    html.append("<div class='board-wrap'>")
    html.append("<table class='board'>")
    html.append("<tr><th class='left'>분반</th>")
    for level, grade, class_no in class_keys:
        html.append(f"<th colspan='3'>{grade}-{class_no}</th>")
    html.append("</tr>")
    html.append("<tr><td class='left'>요일</td>")
    for _ in class_keys:
        html.append("<td class='empty'>학생</td><td class='empty'>토</td><td class='empty'>일</td>")
    html.append("</tr>")

    html.append("<tr><td class='left'>담임</td>")
    for class_key in class_keys:
        teacher = homeroom_map.get(class_key) or "-"
        html.append(f"<td class='name'>{escape(teacher)}</td><td class='mark empty'></td><td class='mark empty'></td>")
    html.append("</tr>")

    html.append("<tr><td class='left'>부담임</td>")
    for class_key in class_keys:
        assistant = assistant_map.get(class_key) or "-"
        html.append(f"<td class='name'>{escape(assistant)}</td><td class='mark empty'></td><td class='mark empty'></td>")
    html.append("</tr>")

    for idx in range(max_students):
        html.append(f"<tr><td class='left'>{idx + 1}</td>")
        for class_key in class_keys:
            students = students_by_class.get(class_key, [])
            if idx < len(students):
                student = students[idx]
                sat_status = status_by_student_day.get(student["student_id"], {}).get("sat", "absent")
                sun_status = status_by_student_day.get(student["student_id"], {}).get("sun", "absent")
                sat_symbol = format_status_symbol(sat_status)
                sun_symbol = format_status_symbol(sun_status)
                sat_class = "mark-present" if sat_status == "present" else "mark-absent"
                sun_class = "mark-present" if sun_status == "present" else "mark-absent"
                html.append(
                    f"<td class='name'>{escape(student['student_name'])}</td>"
                    f"<td class='mark {sat_class}'>{sat_symbol}</td>"
                    f"<td class='mark {sun_class}'>{sun_symbol}</td>"
                )
            else:
                html.append("<td class='empty'></td><td class='empty'></td><td class='empty'></td>")
        html.append("</tr>")

    html.append("</table>")
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def render_weekly_section(
    supabase: Client,
    class_options,
    class_rows,
    class_summary_rows,
    default_anchor: date,
):
    st.subheader("주차별 전체 출석 현황 (일요일 기준)")

    year_options = [default_anchor.year - 1, default_anchor.year, default_anchor.year + 1]
    selected_year = st.selectbox(
        "기준 연도",
        year_options,
        index=1,
        key="week_year",
    )
    selected_month = st.selectbox(
        "기준 월",
        list(range(1, 13)),
        index=default_anchor.month - 1,
        key="week_month",
        format_func=lambda m: f"{m}월",
    )
    selected_week_no = st.selectbox(
        "주차",
        [1, 2, 3, 4, 5],
        index=0,
        key="week_no",
        format_func=lambda w: f"{w}주차",
    )

    month_first = date(selected_year, selected_month, 1)
    next_month_first = (
        date(selected_year + 1, 1, 1)
        if selected_month == 12
        else date(selected_year, selected_month + 1, 1)
    )
    month_last = next_month_first - timedelta(days=1)
    days_to_sunday = (6 - month_first.weekday()) % 7
    first_sunday = month_first + timedelta(days=days_to_sunday)
    sundays = []
    cursor = first_sunday
    while cursor <= month_last:
        sundays.append(cursor)
        cursor += timedelta(days=7)

    if selected_week_no > len(sundays):
        st.warning(f"{selected_year}년 {selected_month}월은 {len(sundays)}주차까지만 있습니다.")
        return

    sunday_date = sundays[selected_week_no - 1]
    saturday_date = sunday_date - timedelta(days=1)

    st.caption(
        f"기준 주차: {selected_year}년 {selected_month}월 {selected_week_no}주차 "
        f"(토 {saturday_date} / 일 {sunday_date})"
    )

    week_class_filter_options = [("전체", 0, 0)] + class_options
    selected_week_class = st.selectbox(
        "주간 반 필터",
        week_class_filter_options,
        index=0,
        format_func=lambda c: "전체" if c[0] == "전체" else f"{c[0]} {c[1]}학년 {c[2]}반",
        key="week_class_filter",
    )

    try:
        weekly_rows = fetch_roster_by_range(supabase, saturday_date, sunday_date)
    except Exception as e:
        st.error(f"주간 조회 실패: {e}")
        return

    weekly_filtered = [
        r
        for r in weekly_rows
        if selected_week_class[0] == "전체"
        or (r["level"], r["grade"], r["class_no"]) == selected_week_class
    ]

    rows_by_student = defaultdict(list)
    for r in weekly_filtered:
        rows_by_student[r["student_id"]].append(r)

    weekly_display = []
    weekly_status_counts = Counter()
    status_by_student_day = defaultdict(dict)

    weekly_students = class_rows
    if selected_week_class[0] != "전체":
        weekly_students = [
            r
            for r in class_rows
            if (r["level"], r["grade"], r["class_no"]) == selected_week_class
        ]

    seen = set()
    unique_weekly_students = []
    for s in weekly_students:
        if s["student_id"] in seen:
            continue
        seen.add(s["student_id"])
        unique_weekly_students.append(s)

    for student in sorted(unique_weekly_students, key=lambda x: (x.get("student_name") or "")):
        student_rows = rows_by_student.get(student["student_id"], [])
        sat_rows = [
            r
            for r in student_rows
            if r["attendance_date"] == saturday_date.isoformat()
        ]
        sun_rows = [
            r
            for r in student_rows
            if r["attendance_date"] == sunday_date.isoformat()
        ]

        sat_status = normalize_status(sat_rows[-1]["status"]) if sat_rows else "absent"
        sun_status = normalize_status(sun_rows[-1]["status"]) if sun_rows else "absent"
        status_by_student_day[student["student_id"]]["sat"] = sat_status
        status_by_student_day[student["student_id"]]["sun"] = sun_status
        weekly_status_counts[f"sat_{sat_status}"] += 1
        weekly_status_counts[f"sun_{sun_status}"] += 1

        weekly_display.append(
            {
                "학생": student["student_name"],
                "반": f"{student['level']} {student['grade']}학년 {student['class_no']}반",
                "토요일상태": sat_status,
                "일요일상태": sun_status,
            }
        )

    if not weekly_display:
        st.info("해당 반의 학생 데이터가 없습니다.")
        return

    homeroom_map = {
        (r["level"], r["grade"], r["class_no"]): r.get("homeroom_teacher")
        for r in class_summary_rows
    }
    assistant_map = {
        (r["level"], r["grade"], r["class_no"]): normalize_assistant_teacher(
            r.get("assistant_teachers")
        )
        for r in class_summary_rows
    }
    students_by_class = defaultdict(list)
    for student in sorted(unique_weekly_students, key=lambda x: (x.get("student_name") or "")):
        key = (student["level"], student["grade"], student["class_no"])
        students_by_class[key].append(student)

    if selected_week_class[0] == "전체":
        level_keys = sorted({(s["level"], s["grade"], s["class_no"]) for s in unique_weekly_students})
        middle_keys = [k for k in level_keys if k[0] == "middle"]
        high_keys = [k for k in level_keys if k[0] == "high"]
        render_class_board(
            "중등부", middle_keys, students_by_class, status_by_student_day, homeroom_map, assistant_map
        )
        render_class_board(
            "고등부", high_keys, students_by_class, status_by_student_day, homeroom_map, assistant_map
        )
    else:
        level_label = "중등부" if selected_week_class[0] == "middle" else "고등부"
        render_class_board(
            level_label,
            [selected_week_class],
            students_by_class,
            status_by_student_day,
            homeroom_map,
            assistant_map,
        )

    with st.expander("주간 원본(검증용)"):
        st.dataframe(weekly_display, use_container_width=True)


st.title("수원교회 중고등부 출석현황")
st.caption("Streamlit + Supabase")

top_actions = st.columns([1, 6])
with top_actions[0]:
    if st.button("🔄", help="데이터 새로고침"):
        st.cache_data.clear()
        st.rerun()

try:
    supabase = get_supabase()
except Exception as e:
    st.error(str(e))
    st.stop()

try:
    all_rows = fetch_all_roster(supabase)
    class_rows = fetch_class_detail(supabase)
    class_summary_rows = fetch_class_summary(supabase)
except Exception as e:
    st.error(f"초기 데이터 조회 실패: {e}")
    st.stop()

level_order = {"middle": 0, "high": 1}
class_options = sorted(
    {(r["level"], r["grade"], r["class_no"]) for r in class_rows},
    key=lambda c: (level_order.get(c[0], 99), c[1], c[2]),
)
if not class_options:
    st.warning("반 정보가 없습니다. v_class_detail 데이터를 확인하세요.")
    st.stop()

if handle_qr_checkin(supabase):
    st.stop()

tab_dashboard, tab_admin = st.tabs(["대시보드", "관리자"])

with tab_dashboard:
    render_weekly_section(
        supabase=supabase,
        class_options=class_options,
        class_rows=class_rows,
        class_summary_rows=class_summary_rows,
        default_anchor=date.today(),
    )

    st.divider()
    st.subheader("전체 출석 데이터")
    if not all_rows:
        st.info("저장된 출석 데이터가 없습니다.")
    else:
        unique_student_ids = {r["student_id"] for r in class_rows}
        unique_students = len(unique_student_ids)
        student_group_map = {}
        for r in class_rows:
            sid = r.get("student_id")
            if not sid:
                continue
            level_name = "중등부" if r.get("level") == "middle" else "고등부"
            sibling = sibling_group(r.get("level"), int(r.get("grade", 0)), int(r.get("class_no", 0)))
            student_group_map[sid] = {"level": level_name, "sibling": sibling}
        date_student_status = defaultdict(dict)
        for row in all_rows:
            sid = row.get("student_id")
            adate = row.get("attendance_date")
            if not sid or not adate or sid not in unique_student_ids:
                continue
            date_student_status[adate][sid] = normalize_status(row.get("status"))

        weekend_counts = Counter()
        for adate, student_map in date_student_status.items():
            day = date.fromisoformat(adate)
            day_code = day_code_from_date(day)
            present_cnt = sum(1 for s in student_map.values() if s == "present")
            absent_cnt = max(unique_students - present_cnt, 0)
            if day_code == "sat":
                weekend_counts["sat_present"] += present_cnt
                weekend_counts["sat_absent"] += absent_cnt
            elif day_code == "sun":
                weekend_counts["sun_present"] += present_cnt
                weekend_counts["sun_absent"] += absent_cnt

        total_cols = st.columns(5)
        total_cols[0].metric("학생 수(교사 제외)", unique_students)
        total_cols[1].metric("토요일 출석", weekend_counts.get("sat_present", 0))
        total_cols[2].metric("토요일 결석", weekend_counts.get("sat_absent", 0))
        total_cols[3].metric("일요일 출석", weekend_counts.get("sun_present", 0))
        total_cols[4].metric("일요일 결석", weekend_counts.get("sun_absent", 0))

        chart_col1, chart_col2 = st.columns(2)
        with chart_col1:
            st.caption("상태별 분포 (토/일 통합)")
            weekend_bar = build_weekend_status_bar_chart(
                sat_present=weekend_counts.get("sat_present", 0),
                sat_absent=weekend_counts.get("sat_absent", 0),
                sun_present=weekend_counts.get("sun_present", 0),
                sun_absent=weekend_counts.get("sun_absent", 0),
            )
            st.plotly_chart(weekend_bar, use_container_width=True, config={"displayModeBar": False})

        with chart_col2:
            st.caption("주차별 출석 인원 (토/일 구분)")
            week_agg = defaultdict(lambda: {"sat_present": 0, "sun_present": 0})
            for adate, student_map in date_student_status.items():
                day = date.fromisoformat(adate)
                day_code = day_code_from_date(day)
                if day_code not in {"sat", "sun"}:
                    continue
                anchor_sunday = day + timedelta(days=1) if day_code == "sat" else day
                week_key = anchor_sunday.isoformat()
                present_cnt = sum(1 for s in student_map.values() if s == "present")
                if day_code == "sat":
                    week_agg[week_key]["sat_present"] += present_cnt
                elif day_code == "sun":
                    week_agg[week_key]["sun_present"] += present_cnt

            week_rows = sorted(week_agg.items(), key=lambda x: x[0])
            weeks = [week_label_from_sunday(date.fromisoformat(k)) for k, _ in week_rows]
            sat_vals = [v["sat_present"] for _, v in week_rows]
            sun_vals = [v["sun_present"] for _, v in week_rows]
            y_max = max(sat_vals + sun_vals + [1]) * 1.35

            trend_fig = go.Figure()
            trend_fig.add_trace(
                go.Scatter(
                    name="토요일",
                    x=weeks,
                    y=sat_vals,
                    mode="lines+markers+text",
                    text=sat_vals,
                    textposition="top center",
                    line=dict(color="#22c55e", width=3),
                    marker=dict(size=8),
                )
            )
            trend_fig.add_trace(
                go.Scatter(
                    name="일요일",
                    x=weeks,
                    y=sun_vals,
                    mode="lines+markers+text",
                    text=sun_vals,
                    textposition="top center",
                    line=dict(color="#f97316", width=3),
                    marker=dict(size=8),
                )
            )
            trend_fig.update_layout(
                yaxis=dict(title="출석 인원(명)", range=[0, y_max]),
                xaxis=dict(title="주차"),
                margin=dict(l=20, r=20, t=20, b=20),
                legend=dict(title="요일"),
                template="plotly_dark",
                height=320,
            )
            st.plotly_chart(trend_fig, use_container_width=True, config={"displayModeBar": False})

        st.subheader("중등부 / 고등부 출석 인원 비교 (토/일)")
        level_student_ids = {
            "중등부": {r["student_id"] for r in class_rows if r.get("level") == "middle"},
            "고등부": {r["student_id"] for r in class_rows if r.get("level") == "high"},
        }
        level_totals = {
            "중등부": len(level_student_ids["중등부"]),
            "고등부": len(level_student_ids["고등부"]),
        }
        all_dates = sorted(date_student_status.keys())
        sat_dates = [d for d in all_dates if day_code_from_date(date.fromisoformat(d)) == "sat"]
        sun_dates = [d for d in all_dates if day_code_from_date(date.fromisoformat(d)) == "sun"]
        latest_sat = sat_dates[-1] if sat_dates else None
        latest_sun = sun_dates[-1] if sun_dates else None

        def level_present_for(adate: str | None, level_name: str) -> int:
            if not adate:
                return 0
            student_map = date_student_status.get(adate, {})
            return sum(
                1
                for sid, stt in student_map.items()
                if sid in level_student_ids[level_name] and stt == "present"
            )

        sat_middle_present = level_present_for(latest_sat, "중등부")
        sat_high_present = level_present_for(latest_sat, "고등부")
        sun_middle_present = level_present_for(latest_sun, "중등부")
        sun_high_present = level_present_for(latest_sun, "고등부")

        level_summary_cols = st.columns(2)
        with level_summary_cols[0]:
            st.markdown("**중등부**")
            middle_metric_cols = st.columns(3)
            middle_metric_cols[0].metric("전체", level_totals["중등부"])
            middle_metric_cols[1].metric("토요일 출석", sat_middle_present)
            middle_metric_cols[2].metric("일요일 출석", sun_middle_present)
        with level_summary_cols[1]:
            st.markdown("**고등부**")
            high_metric_cols = st.columns(3)
            high_metric_cols[0].metric("전체", level_totals["고등부"])
            high_metric_cols[1].metric("토요일 출석", sat_high_present)
            high_metric_cols[2].metric("일요일 출석", sun_high_present)

        level_weekend_present = {
            "중등부": {"sat": 0, "sun": 0},
            "고등부": {"sat": 0, "sun": 0},
        }
        for adate, student_map in date_student_status.items():
            day_code = day_code_from_date(date.fromisoformat(adate))
            if day_code not in {"sat", "sun"}:
                continue
            for level_name, sids in level_student_ids.items():
                present_cnt = sum(
                    1 for sid, stt in student_map.items() if sid in sids and stt == "present"
                )
                level_weekend_present[level_name][day_code] += present_cnt

        level_fig = go.Figure()
        x_levels = ["중등부", "고등부"]
        level_fig.add_trace(
            go.Bar(
                name="토요일",
                x=x_levels,
                y=[
                    level_weekend_present["중등부"]["sat"],
                    level_weekend_present["고등부"]["sat"],
                ],
                text=[
                    level_weekend_present["중등부"]["sat"],
                    level_weekend_present["고등부"]["sat"],
                ],
                textposition="outside",
                marker_color="#22c55e",
                cliponaxis=False,
            )
        )
        level_fig.add_trace(
            go.Bar(
                name="일요일",
                x=x_levels,
                y=[
                    level_weekend_present["중등부"]["sun"],
                    level_weekend_present["고등부"]["sun"],
                ],
                text=[
                    level_weekend_present["중등부"]["sun"],
                    level_weekend_present["고등부"]["sun"],
                ],
                textposition="outside",
                marker_color="#f97316",
                cliponaxis=False,
            )
        )
        max_level_val = max(
            level_weekend_present["중등부"]["sat"] + level_weekend_present["중등부"]["sun"],
            level_weekend_present["고등부"]["sat"] + level_weekend_present["고등부"]["sun"],
            1,
        )
        level_fig.update_layout(
            barmode="group",
            yaxis=dict(title="출석 인원(명)", range=[0, max_level_val * 1.35]),
            xaxis=dict(title="부서"),
            margin=dict(l=20, r=20, t=20, b=20),
            legend=dict(title="요일"),
            template="plotly_dark",
            height=300,
        )
        st.plotly_chart(level_fig, use_container_width=True, config={"displayModeBar": False})

        sibling_total = Counter()
        sibling_by_level = {
            "중등부": Counter(),
            "고등부": Counter(),
        }
        sibling_by_day = {
            "sat": Counter(),
            "sun": Counter(),
        }
        for adate, student_map in date_student_status.items():
            day_code = day_code_from_date(date.fromisoformat(adate))
            for sid, stt in student_map.items():
                if stt != "present":
                    continue
                meta = student_group_map.get(sid, {})
                sibling = meta.get("sibling", "")
                level_name = meta.get("level", "")
                if sibling in {"형제", "자매"}:
                    sibling_total[sibling] += 1
                    if level_name in sibling_by_level:
                        sibling_by_level[level_name][sibling] += 1
                    if day_code in {"sat", "sun"}:
                        sibling_by_day[day_code][sibling] += 1

        sibling_total_counts = {
            "형제": sum(
                1
                for sid in unique_student_ids
                if student_group_map.get(sid, {}).get("sibling") == "형제"
            ),
            "자매": sum(
                1
                for sid in unique_student_ids
                if student_group_map.get(sid, {}).get("sibling") == "자매"
            ),
        }
        sibling_level_total_counts = {
            "중등부": {
                "형제": sum(
                    1
                    for sid in unique_student_ids
                    if student_group_map.get(sid, {}).get("level") == "중등부"
                    and student_group_map.get(sid, {}).get("sibling") == "형제"
                ),
                "자매": sum(
                    1
                    for sid in unique_student_ids
                    if student_group_map.get(sid, {}).get("level") == "중등부"
                    and student_group_map.get(sid, {}).get("sibling") == "자매"
                ),
            },
            "고등부": {
                "형제": sum(
                    1
                    for sid in unique_student_ids
                    if student_group_map.get(sid, {}).get("level") == "고등부"
                    and student_group_map.get(sid, {}).get("sibling") == "형제"
                ),
                "자매": sum(
                    1
                    for sid in unique_student_ids
                    if student_group_map.get(sid, {}).get("level") == "고등부"
                    and student_group_map.get(sid, {}).get("sibling") == "자매"
                ),
            },
        }
        latest_att_date = max(date_student_status.keys()) if date_student_status else None
        latest_att_map = date_student_status.get(latest_att_date, {}) if latest_att_date else {}

        sibling_present_latest = Counter()
        sibling_level_present_latest = {
            "중등부": Counter(),
            "고등부": Counter(),
        }
        for sid, stt in latest_att_map.items():
            if stt != "present":
                continue
            meta = student_group_map.get(sid, {})
            sibling = meta.get("sibling", "")
            level_name = meta.get("level", "")
            if sibling in {"형제", "자매"}:
                sibling_present_latest[sibling] += 1
                if level_name in sibling_level_present_latest:
                    sibling_level_present_latest[level_name][sibling] += 1

        sib_col1, sib_col2 = st.columns(2)
        with sib_col1:
            st.subheader("전체 형제/자매 출석 통계")
            total_metric_cols = st.columns(2)
            total_metric_cols[0].metric(
                "전체 형제 출석",
                f"{sibling_present_latest.get('형제', 0)}/{sibling_total_counts['형제']}",
            )
            total_metric_cols[1].metric(
                "전체 자매 출석",
                f"{sibling_present_latest.get('자매', 0)}/{sibling_total_counts['자매']}",
            )
            total_fig = go.Figure()
            x_total = ["토요일", "일요일"]
            y_total_brother = [
                sibling_by_day["sat"].get("형제", 0),
                sibling_by_day["sun"].get("형제", 0),
            ]
            y_total_sister = [
                sibling_by_day["sat"].get("자매", 0),
                sibling_by_day["sun"].get("자매", 0),
            ]
            total_fig.add_trace(
                go.Bar(
                    name="형제",
                    x=x_total,
                    y=y_total_brother,
                    text=y_total_brother,
                    textposition="outside",
                    marker_color="#38bdf8",
                    cliponaxis=False,
                )
            )
            total_fig.add_trace(
                go.Bar(
                    name="자매",
                    x=x_total,
                    y=y_total_sister,
                    text=y_total_sister,
                    textposition="outside",
                    marker_color="#f97316",
                    cliponaxis=False,
                )
            )
            total_max = max(y_total_brother + y_total_sister + [1])
            total_fig.update_layout(
                barmode="group",
                yaxis=dict(title="출석 인원(명)", range=[0, total_max * 1.35]),
                xaxis=dict(title="요일"),
                margin=dict(l=20, r=20, t=20, b=20),
                template="plotly_dark",
                height=300,
                legend=dict(title="구분"),
            )
            st.plotly_chart(total_fig, use_container_width=True, config={"displayModeBar": False})

        with sib_col2:
            st.subheader("중등부/고등부별 형제/자매 출석 통계")
            level_sibling_metrics = st.columns(4)
            level_sibling_metrics[0].metric(
                "중등부 형제 출석",
                f"{sibling_level_present_latest['중등부'].get('형제', 0)}/{sibling_level_total_counts['중등부']['형제']}",
            )
            level_sibling_metrics[1].metric(
                "중등부 자매 출석",
                f"{sibling_level_present_latest['중등부'].get('자매', 0)}/{sibling_level_total_counts['중등부']['자매']}",
            )
            level_sibling_metrics[2].metric(
                "고등부 형제 출석",
                f"{sibling_level_present_latest['고등부'].get('형제', 0)}/{sibling_level_total_counts['고등부']['형제']}",
            )
            level_sibling_metrics[3].metric(
                "고등부 자매 출석",
                f"{sibling_level_present_latest['고등부'].get('자매', 0)}/{sibling_level_total_counts['고등부']['자매']}",
            )
            level_sib_fig = go.Figure()
            x_levels = ["중등부", "고등부"]
            y_brother = [
                sibling_by_level["중등부"].get("형제", 0),
                sibling_by_level["고등부"].get("형제", 0),
            ]
            y_sister = [
                sibling_by_level["중등부"].get("자매", 0),
                sibling_by_level["고등부"].get("자매", 0),
            ]
            level_sib_fig.add_trace(
                go.Bar(
                    name="형제",
                    x=x_levels,
                    y=y_brother,
                    text=y_brother,
                    textposition="outside",
                    marker_color="#38bdf8",
                    cliponaxis=False,
                )
            )
            level_sib_fig.add_trace(
                go.Bar(
                    name="자매",
                    x=x_levels,
                    y=y_sister,
                    text=y_sister,
                    textposition="outside",
                    marker_color="#f97316",
                    cliponaxis=False,
                )
            )
            level_sib_max = max(y_brother + y_sister + [1])
            level_sib_fig.update_layout(
                barmode="group",
                yaxis=dict(title="출석 인원(명)", range=[0, level_sib_max * 1.35]),
                xaxis=dict(title="부서"),
                margin=dict(l=20, r=20, t=20, b=20),
                legend=dict(title="구분"),
                template="plotly_dark",
                height=300,
            )
            st.plotly_chart(level_sib_fig, use_container_width=True, config={"displayModeBar": False})

with tab_admin:
    st.markdown("#### 수동 출석 입력")
    manual_date = st.date_input("수동 출석 날짜", value=date.today(), key="manual_date_input")
    manual_day_label = day_label_from_date(manual_date)
    st.caption(f"선택한 날짜 요일: {manual_day_label}")

    manual_class = st.selectbox(
        "수동 출석 반 선택",
        class_options,
        format_func=lambda c: f"{c[0]} {c[1]}학년 {c[2]}반",
        key="manual_class_select",
    )

    manual_students = [
        r for r in class_rows if (r["level"], r["grade"], r["class_no"]) == manual_class
    ]
    manual_student_options = {
        f"{r['student_name']} ({r['student_id'][:8]})": r for r in manual_students
    }

    if not manual_student_options:
        st.info("선택한 반에 학생 정보가 없습니다.")
    else:
        manual_cols = st.columns([3, 1])
        with manual_cols[0]:
            selected_manual_student = st.selectbox(
                "학생",
                list(manual_student_options.keys()),
                key="manual_student_pick",
            )
        with manual_cols[1]:
            submit_manual = st.button("수동 출석 등록", use_container_width=True, key="manual_submit")

        if submit_manual:
            if day_code_from_date(manual_date) not in {"sat", "sun"}:
                st.warning("수동 출석 등록은 토요일/일요일만 가능합니다.")
            else:
                student = manual_student_options[selected_manual_student]
                school_class_id = find_school_class_id_by_student_id(supabase, student["student_id"])
                if not school_class_id:
                    st.error("student_class에서 반 정보를 찾지 못했습니다.")
                else:
                    try:
                        save_attendance(
                            client=supabase,
                            attendance_date=manual_date,
                            student_id=student["student_id"],
                            school_class_id=school_class_id,
                            status="present",
                            note="manual check-in",
                        )
                        st.success(f"수동 등록 완료: {student['student_name']}")
                    except Exception as e:
                        st.error(f"수동 등록 실패: {e}")

    st.markdown("#### QR 출석 링크 생성")
    qr_cols = st.columns(3)
    with qr_cols[0]:
        qr_date = st.date_input("QR 날짜", value=date.today(), key="qr_date_input")
    with qr_cols[1]:
        st.empty()
    with qr_cols[2]:
        st.empty()

    app_base_url = resolve_app_base_url()
    app_tz = get_app_timezone()
    active_qr_slot = current_qr_slot(app_tz)
    slot_start = datetime.now(app_tz).replace(minute=0, second=0, microsecond=0)
    slot_end = slot_start + timedelta(hours=1)

    if day_code_from_date(qr_date) not in {"sat", "sun"}:
        st.info("QR 날짜는 토요일/일요일만 선택하세요.")
    else:
        qr_url = build_qr_checkin_url(
            base_url=app_base_url,
            attendance_date=qr_date,
            status="present",
            qr_slot=active_qr_slot,
        )
        st.caption(
            f"현재 QR 유효시간: {slot_start.strftime('%H:%M')} ~ {slot_end.strftime('%H:%M')} ({app_tz})"
        )
        st.code(qr_url)
        if app_base_url:
            st.image(
                f"https://quickchart.io/qr?size=170&text={quote_plus(qr_url)}",
                caption="학생이 스캔한 뒤 이름 입력 + source_key 선택으로 출석 처리됩니다.",
                width=170,
            )
        else:
            st.warning(
                "앱 URL을 자동으로 찾지 못했습니다. Streamlit Secrets에 "
                "`APP_BASE_URL = \"https://<app>.streamlit.app\"`를 추가하세요."
            )
