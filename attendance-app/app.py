import os
from collections import Counter, defaultdict
from datetime import date, timedelta
from html import escape
from urllib.parse import urlencode, quote_plus

import altair as alt
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


def find_student_and_class_by_name(
    client: Client, student_name: str, school_class_id_hint: str = ""
):
    students = (
        client.table("student")
        .select("id,name")
        .eq("name", student_name)
        .execute()
    ).data or []
    if not students:
        return ("", "")

    matches = []
    for s in students:
        links = (
            client.table("student_class")
            .select("student_id,school_class_id")
            .eq("student_id", s["id"])
            .execute()
        ).data or []
        for link in links:
            if school_class_id_hint and link["school_class_id"] != school_class_id_hint:
                continue
            matches.append((s["id"], link["school_class_id"]))

    if len(matches) == 1:
        return matches[0]
    return ("", "")


def handle_qr_checkin(supabase: Client):
    qr_date = get_query_value("qr_date").strip()
    qr_status = normalize_status(get_query_value("qr_status").strip() or "present")
    qr_school_class_id = get_query_value("qr_school_class_id").strip()
    qr_source = get_query_value("source").strip()

    if qr_source != "qr":
        return False
    if not qr_date or not qr_school_class_id:
        st.error("QR 링크 파라미터가 누락되었습니다.")
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
    submit = st.button("출석하기")

    if submit:
        if not student_name_input.strip():
            st.warning("이름을 입력하세요.")
            return True
        try:
            student_id, school_class_id = find_student_and_class_by_name(
                supabase, student_name_input.strip(), qr_school_class_id
            )
            if not student_id or not school_class_id:
                st.error("이름과 일치하는 학생/반 정보를 찾지 못했습니다. (동명이인 포함)")
                return True

            save_attendance(
                client=supabase,
                attendance_date=attendance_date,
                student_id=student_id,
                school_class_id=school_class_id,
                status=qr_status,
                note="QR check-in (name)",
            )
            st.success(f"출석 완료: {student_name_input.strip()}")
        except Exception as e:
            st.error(f"QR 출석 처리 실패: {e}")

    return True


def build_qr_checkin_url(base_url: str, school_class_id: str, attendance_date: date, status: str) -> str:
    params = urlencode(
        {
            "source": "qr",
            "qr_school_class_id": school_class_id,
            "qr_date": attendance_date.isoformat(),
            "qr_status": normalize_status(status),
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


def build_day_pie_chart(day_label: str, present_count: int, absent_count: int):
    data = [
        {"status": "present", "count": present_count},
        {"status": "absent", "count": absent_count},
    ]
    base = (
        alt.Chart(alt.Data(values=data))
        .transform_joinaggregate(total="sum(count)")
        .transform_calculate(percent="datum.total > 0 ? datum.count / datum.total * 100 : 0")
    )
    pie = base.mark_arc(innerRadius=42, outerRadius=110).encode(
        theta=alt.Theta("count:Q"),
        color=alt.Color(
            "status:N",
            scale=alt.Scale(domain=["present", "absent"], range=["#0ea5e9", "#ef4444"]),
            legend=None,
        ),
        tooltip=[
            alt.Tooltip("status:N", title="상태"),
            alt.Tooltip("count:Q", title="인원"),
            alt.Tooltip("percent:Q", title="비율(%)", format=".1f"),
        ],
    )
    return (
        alt.layer(pie)
        .properties(title=day_label)
        .configure_view(strokeOpacity=0)
    )


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
    html.append("<style>.board{border-collapse:collapse;width:100%;font-size:12px;table-layout:auto}")
    html.append(".board th,.board td{border:1px solid #444;padding:3px 4px;text-align:center;white-space:nowrap}")
    html.append(".board th{background:#1f2937;color:#fff}")
    html.append(".board .left{background:#111827;color:#fff;min-width:48px}")
    html.append(".board .name{background:#0f172a;color:#e5e7eb;text-align:left;font-size:11px}")
    html.append(".board .mark{font-weight:700;min-width:30px}")
    html.append(".board .mark-present{background:#0ea5e9;color:#001018}")
    html.append(".board .mark-absent{background:#ef4444;color:#ffffff}")
    html.append(".board .empty{background:#1f2937;color:#6b7280}")
    html.append("</style>")

    html.append(f"<h4>{escape(level_name)}</h4>")
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

    w_cols = st.columns(5)
    w_cols[0].metric("학생 수(교사 제외)", len(unique_weekly_students))
    w_cols[1].metric("토 출석", weekly_status_counts.get("sat_present", 0))
    w_cols[2].metric("토 결석", weekly_status_counts.get("sat_absent", 0))
    w_cols[3].metric("일 출석", weekly_status_counts.get("sun_present", 0))
    w_cols[4].metric("일 결석", weekly_status_counts.get("sun_absent", 0))

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
    weekend_counts = Counter()
    for adate, student_map in date_student_status.items():
        day = date.fromisoformat(adate)
        day_code = day_code_from_date(day)
        present_cnt = sum(1 for s in student_map.values() if s == "present")
        absent_cnt = max(unique_students - present_cnt, 0)
        status_counts["present"] += present_cnt
        status_counts["absent"] += absent_cnt
        date_counts[adate] = present_cnt + absent_cnt
        if day_code == "sat":
            weekend_counts["sat_present"] += present_cnt
            weekend_counts["sat_absent"] += absent_cnt
        elif day_code == "sun":
            weekend_counts["sun_present"] += present_cnt
            weekend_counts["sun_absent"] += absent_cnt

    metric_cols = st.columns(5)
    metric_cols[0].metric("학생 수(교사 제외)", unique_students)
    metric_cols[1].metric("토요일 출석", weekend_counts.get("sat_present", 0))
    metric_cols[2].metric("토요일 결석", weekend_counts.get("sat_absent", 0))
    metric_cols[3].metric("일요일 출석", weekend_counts.get("sun_present", 0))
    metric_cols[4].metric("일요일 결석", weekend_counts.get("sun_absent", 0))

    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        st.caption("상태별 분포 (토/일 분리)")
        pie_cols = st.columns(2)
        with pie_cols[0]:
            sat_pie = build_day_pie_chart(
                "토요일",
                weekend_counts.get("sat_present", 0),
                weekend_counts.get("sat_absent", 0),
            )
            st.altair_chart(sat_pie, use_container_width=True)
        with pie_cols[1]:
            sun_pie = build_day_pie_chart(
                "일요일",
                weekend_counts.get("sun_present", 0),
                weekend_counts.get("sun_absent", 0),
            )
            st.altair_chart(sun_pie, use_container_width=True)
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

        weekly_rate_data = []
        for idx, (week_key, agg) in enumerate(sorted(week_agg.items(), key=lambda x: x[0])):
            week_start_date = date.fromisoformat(week_key)
            week_label = week_label_from_sunday(week_start_date)
            weekly_rate_data.append(
                {
                    "week": week_label,
                    "week_order": idx,
                    "day_type": "토요일",
                    "attendance_count": agg["sat_present"],
                }
            )
            weekly_rate_data.append(
                {
                    "week": week_label,
                    "week_order": idx,
                    "day_type": "일요일",
                    "attendance_count": agg["sun_present"],
                }
            )

        weekly_count_chart = (
            alt.Chart(alt.Data(values=weekly_rate_data))
            .mark_line(point=True, strokeWidth=3)
            .encode(
                x=alt.X("week:N", title="주차", sort=alt.SortField(field="week_order", order="ascending")),
                y=alt.Y("attendance_count:Q", title="출석 인원(명)"),
                color=alt.Color(
                    "day_type:N",
                    scale=alt.Scale(domain=["토요일", "일요일"], range=["#22c55e", "#f97316"]),
                    legend=alt.Legend(title="요일"),
                ),
                tooltip=["week:N", "day_type:N", "attendance_count:Q"],
            )
        )
        weekly_count_text = (
            alt.Chart(alt.Data(values=weekly_rate_data))
            .mark_text(dy=-10, color="white", size=11)
            .encode(
                x=alt.X("week:N", sort=alt.SortField(field="week_order", order="ascending")),
                y=alt.Y("attendance_count:Q"),
                color=alt.Color(
                    "day_type:N",
                    scale=alt.Scale(domain=["토요일", "일요일"], range=["#22c55e", "#f97316"]),
                    legend=None,
                ),
                text=alt.Text("attendance_count:Q"),
            )
        )
        st.altair_chart(alt.layer(weekly_count_chart, weekly_count_text), use_container_width=True)

st.divider()
st.subheader("출석 입력")

selected_date = st.date_input("출석 날짜", value=date.today())
selected_date_label = day_label_from_date(selected_date)
st.caption(f"선택한 날짜 요일: {selected_date_label}")
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
    if day_code_from_date(selected_date) not in {"sat", "sun"}:
        st.error("출석 입력은 토요일/일요일만 가능합니다. 날짜를 주말로 선택하세요.")
    else:
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

st.markdown("#### QR 출석 링크 생성")
qr_cols = st.columns(3)
with qr_cols[0]:
    qr_date = st.date_input("QR 날짜", value=selected_date, key="qr_date_input")
with qr_cols[1]:
    qr_class_for_link = st.selectbox(
        "QR 반",
        class_options,
        format_func=lambda c: f"{c[0]} {c[1]}학년 {c[2]}반",
        key="qr_class_for_link",
    )
with qr_cols[2]:
    qr_status = st.selectbox("QR 상태", ["present", "absent"], index=0, key="qr_status")

app_base_url = resolve_app_base_url()

if day_code_from_date(qr_date) not in {"sat", "sun"}:
    st.info("QR 날짜는 토요일/일요일만 선택하세요.")
else:
    qr_class_id = class_id_map.get(qr_class_for_link, "")
    if not qr_class_id:
        st.warning("선택한 반의 school_class_id를 찾지 못했습니다.")
    else:
        qr_url = build_qr_checkin_url(
            base_url=app_base_url,
            school_class_id=qr_class_id,
            attendance_date=qr_date,
            status=qr_status,
        )
        st.code(qr_url)
        if app_base_url:
            st.image(
                f"https://quickchart.io/qr?size=220&text={quote_plus(qr_url)}",
                caption="학생이 스캔하면 이름 입력 후 출석 처리됩니다.",
            )
        else:
            st.warning(
                "앱 URL을 자동으로 찾지 못했습니다. Streamlit Secrets에 "
                "`APP_BASE_URL = \"https://<app>.streamlit.app\"`를 추가하세요."
            )

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
