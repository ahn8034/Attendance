import os
from collections import Counter, defaultdict
from datetime import date, timedelta
from html import escape

import streamlit as st
from supabase import Client, create_client


st.set_page_config(page_title="출석부", page_icon="✅", layout="wide")


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


def fetch_school_class_map(client: Client):
    rows = (
        client.table("school_class")
        .select("id, level, grade, class_no")
        .execute()
    ).data or []
    return {(r["level"], r["grade"], r["class_no"]): r["id"] for r in rows}


def save_attendance(
    client: Client,
    attendance_date: date,
    student_id: str,
    school_class_id: str,
    status: str,
    note: str,
) -> None:
    payload = {
        "attendance_date": attendance_date.isoformat(),
        "student_id": student_id,
        "school_class_id": school_class_id,
        "status": status,
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


def render_class_board(
    level_name: str,
    class_keys,
    students_by_class,
    status_by_student,
    homeroom_map,
    assistant_map,
):
    if not class_keys:
        return

    max_students = max(len(students_by_class.get(c, [])) for c in class_keys)

    html = []
    html.append("<style>.board{border-collapse:collapse;width:100%;font-size:14px}")
    html.append(".board th,.board td{border:1px solid #444;padding:4px 6px;text-align:center}")
    html.append(".board th{background:#1f2937;color:#fff}")
    html.append(".board .left{background:#111827;color:#fff;min-width:48px}")
    html.append(".board .name{background:#0f172a;color:#e5e7eb;text-align:left}")
    html.append(".board .mark{background:#0ea5e9;color:#001018;font-weight:700;min-width:30px}")
    html.append(".board .empty{background:#1f2937;color:#6b7280}")
    html.append("</style>")

    html.append(f"<h4>{escape(level_name)}</h4>")
    html.append("<table class='board'>")
    html.append("<tr><th class='left'>분반</th>")
    for level, grade, class_no in class_keys:
        html.append(f"<th colspan='2'>{grade}-{class_no}</th>")
    html.append("</tr>")

    html.append("<tr><td class='left'>담임</td>")
    for class_key in class_keys:
        teacher = homeroom_map.get(class_key) or "-"
        html.append(f"<td class='name'>{escape(teacher)}</td><td class='mark empty'></td>")
    html.append("</tr>")

    html.append("<tr><td class='left'>부담임</td>")
    for class_key in class_keys:
        assistant = assistant_map.get(class_key) or "-"
        html.append(f"<td class='name'>{escape(assistant)}</td><td class='mark empty'></td>")
    html.append("</tr>")

    for idx in range(max_students):
        html.append(f"<tr><td class='left'>{idx + 1}</td>")
        for class_key in class_keys:
            students = students_by_class.get(class_key, [])
            if idx < len(students):
                student = students[idx]
                symbol = format_status_symbol(status_by_student.get(student["student_id"], "absent"))
                html.append(
                    f"<td class='name'>{escape(student['student_name'])}</td><td class='mark'>{symbol}</td>"
                )
            else:
                html.append("<td class='empty'></td><td class='empty'></td>")
        html.append("</tr>")

    html.append("</table>")
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

    week_start = sundays[selected_week_no - 1]
    week_end = week_start + timedelta(days=6)

    st.caption(f"기준 주차: {selected_year}년 {selected_month}월 {selected_week_no}주차 ({week_start} ~ {week_end})")

    week_class_filter_options = [("전체", 0, 0)] + class_options
    selected_week_class = st.selectbox(
        "주간 반 필터",
        week_class_filter_options,
        index=0,
        format_func=lambda c: "전체" if c[0] == "전체" else f"{c[0]} {c[1]}학년 {c[2]}반",
        key="week_class_filter",
    )

    try:
        weekly_rows = fetch_roster_by_range(supabase, week_start, week_end)
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
    sunday_iso = week_start.isoformat()
    status_by_student = {}

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
        sunday_row = next((r for r in student_rows if r["attendance_date"] == sunday_iso), None)
        latest_row = max(student_rows, key=lambda x: x["attendance_date"]) if student_rows else None
        chosen = sunday_row or latest_row

        status = normalize_status(chosen["status"]) if chosen else "absent"
        status_by_student[student["student_id"]] = status
        weekly_status_counts[status] += 1

        weekly_display.append(
            {
                "학생": student["student_name"],
                "반": f"{student['level']} {student['grade']}학년 {student['class_no']}반",
                "주간상태": status,
                "기록일": chosen["attendance_date"] if chosen else "-",
                "비고": chosen.get("note") if chosen else None,
            }
        )

    if not weekly_display:
        st.info("해당 반의 학생 데이터가 없습니다.")
        return

    w_cols = st.columns(3)
    w_cols[0].metric("학생 수(교사 제외)", len(unique_weekly_students))
    w_cols[1].metric("출석", weekly_status_counts.get("present", 0))
    w_cols[2].metric("결석", weekly_status_counts.get("absent", 0))

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
            "중등부", middle_keys, students_by_class, status_by_student, homeroom_map, assistant_map
        )
        render_class_board(
            "고등부", high_keys, students_by_class, status_by_student, homeroom_map, assistant_map
        )
    else:
        level_label = "중등부" if selected_week_class[0] == "middle" else "고등부"
        render_class_board(
            level_label,
            [selected_week_class],
            students_by_class,
            status_by_student,
            homeroom_map,
            assistant_map,
        )

    with st.expander("주간 원본(검증용)"):
        st.dataframe(weekly_display, use_container_width=True)


st.title("출석부 앱")
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
    class_id_map = fetch_school_class_map(supabase)
except Exception as e:
    st.error(f"초기 데이터 조회 실패: {e}")
    st.stop()

class_options = sorted({(r["level"], r["grade"], r["class_no"]) for r in class_rows})
if not class_options:
    st.warning("반 정보가 없습니다. v_class_detail 데이터를 확인하세요.")
    st.stop()

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
    date_student_status = defaultdict(dict)
    for row in all_rows:
        sid = row.get("student_id")
        adate = row.get("attendance_date")
        if not sid or not adate or sid not in unique_student_ids:
            continue
        date_student_status[adate][sid] = normalize_status(row.get("status"))

    status_counts = Counter()
    date_counts = {}
    for adate, student_map in date_student_status.items():
        present_cnt = sum(1 for s in student_map.values() if s == "present")
        absent_cnt = max(unique_students - present_cnt, 0)
        status_counts["present"] += present_cnt
        status_counts["absent"] += absent_cnt
        date_counts[adate] = present_cnt + absent_cnt

    metric_cols = st.columns(4)
    metric_cols[0].metric("전체 기록", sum(date_counts.values()))
    metric_cols[1].metric("학생 수(교사 제외)", unique_students)
    metric_cols[2].metric("출석", status_counts.get("present", 0))
    metric_cols[3].metric("결석", status_counts.get("absent", 0))

    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        st.caption("상태별 분포")
        st.bar_chart(
            {
                "count": {
                    "present": status_counts.get("present", 0),
                    "absent": status_counts.get("absent", 0),
                }
            }
        )
    with chart_col2:
        st.caption("날짜별 기록 수")
        st.line_chart({"count": dict(sorted(date_counts.items(), key=lambda x: x[0]))})

    st.caption("원본 데이터")
    st.dataframe(all_rows, use_container_width=True)

st.divider()
st.subheader("출석 입력")

selected_date = st.date_input("출석 날짜", value=date.today())
selected_class = st.selectbox(
    "반 선택",
    class_options,
    format_func=lambda c: f"{c[0]} {c[1]}학년 {c[2]}반",
)

class_students = [
    r for r in class_rows if (r["level"], r["grade"], r["class_no"]) == selected_class
]
student_options = {f"{r['student_name']} ({r['student_id'][:8]})": r for r in class_students}

with st.form("attendance_form", clear_on_submit=True):
    selected_label = st.selectbox("학생", list(student_options.keys()))
    status = st.selectbox("상태", ["present", "absent"], index=0)
    note = st.text_input("비고", placeholder="선택")
    submitted = st.form_submit_button("저장")

if submitted:
    student = student_options[selected_label]
    school_class_id = class_id_map.get(selected_class)

    if not school_class_id:
        st.error("선택한 반의 school_class_id를 찾지 못했습니다.")
    else:
        try:
            save_attendance(
                client=supabase,
                attendance_date=selected_date,
                student_id=student["student_id"],
                school_class_id=school_class_id,
                status=status,
                note=note,
            )
            st.success(f"저장 완료: {student['student_name']} ({status})")
        except Exception as e:
            st.error(f"저장 실패: {e}")

st.divider()
st.subheader(f"{selected_date} 출석 현황")

try:
    daily_rows = fetch_roster_by_date(supabase, selected_date)
except Exception as e:
    st.error(f"조회 실패: {e}")
    st.stop()

filtered_rows = [
    r
    for r in daily_rows
    if (r["level"], r["grade"], r["class_no"]) == selected_class
]

if not filtered_rows:
    st.info("해당 날짜/반의 출석 데이터가 없습니다.")
else:
    summary_cols = st.columns(2)
    present_cnt = sum(1 for r in filtered_rows if normalize_status(r["status"]) == "present")
    absent_cnt = len(class_students) - present_cnt

    summary_cols[0].metric("출석", present_cnt)
    summary_cols[1].metric("결석", max(absent_cnt, 0))

    st.dataframe(filtered_rows, use_container_width=True)
