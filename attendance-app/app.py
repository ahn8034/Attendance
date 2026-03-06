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

.dept-title {
  font-size: 1.6rem;
  font-weight: 800;
  margin-bottom: 0.25rem;
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


def fetch_class_teacher_ids(client: Client):
    result = client.table("class_teacher").select("teacher_id").execute()
    rows = result.data or []
    return sorted({r.get("teacher_id") for r in rows if r.get("teacher_id")})


def fetch_teacher_list(client: Client):
    result = client.table("teacher").select("id, name").execute()
    rows = result.data or []
    return [r for r in rows if r.get("id") and r.get("name")]


def fetch_teacher_attendance_by_range(client: Client, start_date: date, end_date: date):
    result = (
        client.table("teacher_attendance")
        .select("attendance_date, teacher_id")
        .gte("attendance_date", start_date.isoformat())
        .lte("attendance_date", end_date.isoformat())
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


def delete_attendance(client: Client, attendance_date: date, student_id: str) -> None:
    client.table("attendance").delete().eq("attendance_date", attendance_date.isoformat()).eq(
        "student_id", student_id
    ).execute()


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


def fetch_teachers_by_name(client: Client, teacher_name: str):
    if not teacher_name.strip():
        return []
    rows = (
        client.table("teacher")
        .select("id,name")
        .eq("name", teacher_name.strip())
        .order("name")
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


def find_school_class_id(client: Client, level: str, grade: int, class_no: int) -> str:
    rows = (
        client.table("school_class")
        .select("id")
        .eq("level", level)
        .eq("grade", grade)
        .eq("class_no", class_no)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        return ""
    return rows[0].get("id", "")


def create_student_with_class(
    client: Client,
    student_name: str,
    school_class_id: str,
) -> str:
    payload = {"name": student_name.strip()}

    created = client.table("student").insert(payload).execute().data or []
    if not created or not created[0].get("id"):
        raise RuntimeError("student 생성 결과에서 id를 받지 못했습니다.")

    student_id = created[0]["id"]
    link_payload = {"student_id": student_id, "school_class_id": school_class_id}
    try:
        client.table("student_class").upsert(link_payload, on_conflict="student_id,school_class_id").execute()
    except Exception:
        client.table("student_class").insert(link_payload).execute()
    return student_id


def delete_student_with_related(client: Client, student_id: str) -> None:
    # 학생 출석/반연결을 먼저 제거한 뒤 학생 마스터를 삭제한다.
    client.table("attendance").delete().eq("student_id", student_id).execute()
    client.table("student_class").delete().eq("student_id", student_id).execute()
    client.table("student").delete().eq("id", student_id).execute()


def save_teacher_attendance(client: Client, attendance_date: date, teacher_id: str) -> None:
    payload = {
        "attendance_date": attendance_date.isoformat(),
        "teacher_id": teacher_id,
    }

    try:
        client.table("teacher_attendance").upsert(payload, on_conflict="attendance_date,teacher_id").execute()
        return
    except Exception:
        pass

    existing = (
        client.table("teacher_attendance")
        .select("teacher_id")
        .eq("attendance_date", attendance_date.isoformat())
        .eq("teacher_id", teacher_id)
        .limit(1)
        .execute()
    ).data or []
    if not existing:
        client.table("teacher_attendance").insert(payload).execute()


def delete_teacher_attendance(client: Client, attendance_date: date, teacher_id: str) -> None:
    client.table("teacher_attendance").delete().eq("attendance_date", attendance_date.isoformat()).eq(
        "teacher_id", teacher_id
    ).execute()


def handle_qr_checkin(supabase: Client):
    qr_date = get_query_value("qr_date").strip()
    qr_status = "present"
    qr_slot = get_query_value("qr_slot").strip()
    qr_source = get_query_value("source").strip()
    qr_target = get_query_value("target").strip() or "student"

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

    if qr_target not in {"student", "teacher"}:
        st.error("QR 링크 대상(target)이 올바르지 않습니다.")
        return True

    if qr_target == "teacher":
        st.title("QR 선생님 출석 체크인")
        st.caption(f"{attendance_date} / {day_label_from_date(attendance_date)}")
        teacher_name_input = st.text_input("이름을 입력하세요", placeholder="예: 송영환")
        teacher_candidates = fetch_teachers_by_name(supabase, teacher_name_input)
        if teacher_name_input.strip() and not teacher_candidates:
            st.warning("이름과 일치하는 선생님을 찾지 못했습니다.")
        selected_teacher = None
        if teacher_candidates:
            teacher_labels = [f"{c['name']} ({c['id'][:8]})" for c in teacher_candidates]
            selected_teacher_label = st.selectbox(
                "선생님 선택",
                teacher_labels,
                key="qr_teacher_pick",
            )
            selected_teacher = teacher_candidates[teacher_labels.index(selected_teacher_label)]
        submit_teacher = st.button("출석하기", key="qr_teacher_submit")

        if submit_teacher:
            if not teacher_name_input.strip():
                st.warning("이름을 입력하세요.")
                return True
            if not selected_teacher:
                st.warning("선생님을 선택하세요.")
                return True
            try:
                save_teacher_attendance(
                    client=supabase,
                    attendance_date=attendance_date,
                    teacher_id=selected_teacher["id"],
                )
                st.success(f"선생님 출석 완료: {selected_teacher['name']}")
            except Exception as e:
                st.error(f"선생님 QR 출석 처리 실패: {e}")
        return True

    st.title("QR 학생 출석 체크인")
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


def build_qr_checkin_url(
    base_url: str, attendance_date: date, status: str, qr_slot: str, target: str = "student"
) -> str:
    params = urlencode(
        {
            "source": "qr",
            "qr_date": attendance_date.isoformat(),
            "qr_status": "present",
            "qr_slot": qr_slot,
            "target": target,
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


def attendance_pair_marker_colors(
    sat_vals: list[int],
    sun_vals: list[int],
    total_count: int,
    sat_default_color: str,
    sun_default_color: str,
) -> tuple[list[str], list[str]]:
    sat_colors = []
    sun_colors = []
    for sat_v, sun_v in zip(sat_vals, sun_vals):
        if total_count > 0 and sat_v == total_count and sun_v == total_count:
            sat_colors.append("#0ea5e9")  # 토/일 모두 출석
            sun_colors.append("#0ea5e9")
        elif sat_v == 0 and sun_v == 0:
            sat_colors.append("#ef4444")  # 토/일 모두 결석
            sun_colors.append("#ef4444")
        else:
            sat_colors.append(sat_default_color)
            sun_colors.append(sun_default_color)
    return sat_colors, sun_colors


def render_attendance_color_guide():
    st.caption("색상 안내: 파란색 점=토/일 모두 출석, 빨간색 점=토/일 모두 결석, 그 외 점=요일 기본색")


def render_class_board(
    level_name: str,
    class_keys,
    students_by_class,
    status_by_student_day,
    homeroom_map,
    assistant_map,
    homeroom_status_map=None,
    assistant_status_map=None,
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
        sat_status = (homeroom_status_map or {}).get(class_key, {}).get("sat")
        sun_status = (homeroom_status_map or {}).get(class_key, {}).get("sun")
        if sat_status in {"present", "absent"}:
            sat_symbol = format_status_symbol(sat_status)
            sat_class = "mark-present" if sat_status == "present" else "mark-absent"
            sat_cell = f"<td class='mark {sat_class}'>{sat_symbol}</td>"
        else:
            sat_cell = "<td class='mark empty'></td>"
        if sun_status in {"present", "absent"}:
            sun_symbol = format_status_symbol(sun_status)
            sun_class = "mark-present" if sun_status == "present" else "mark-absent"
            sun_cell = f"<td class='mark {sun_class}'>{sun_symbol}</td>"
        else:
            sun_cell = "<td class='mark empty'></td>"
        html.append(f"<td class='name'>{escape(teacher)}</td>{sat_cell}{sun_cell}")
    html.append("</tr>")

    html.append("<tr><td class='left'>부담임</td>")
    for class_key in class_keys:
        assistant = assistant_map.get(class_key) or "-"
        sat_status = (assistant_status_map or {}).get(class_key, {}).get("sat")
        sun_status = (assistant_status_map or {}).get(class_key, {}).get("sun")
        if sat_status in {"present", "absent"}:
            sat_symbol = format_status_symbol(sat_status)
            sat_class = "mark-present" if sat_status == "present" else "mark-absent"
            sat_cell = f"<td class='mark {sat_class}'>{sat_symbol}</td>"
        else:
            sat_cell = "<td class='mark empty'></td>"
        if sun_status in {"present", "absent"}:
            sun_symbol = format_status_symbol(sun_status)
            sun_class = "mark-present" if sun_status == "present" else "mark-absent"
            sun_cell = f"<td class='mark {sun_class}'>{sun_symbol}</td>"
        else:
            sun_cell = "<td class='mark empty'></td>"
        html.append(f"<td class='name'>{escape(assistant)}</td>{sat_cell}{sun_cell}")
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
        return None

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
        return None

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
        return None

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

    homeroom_status_map = defaultdict(dict)
    assistant_status_map = defaultdict(dict)
    try:
        teacher_rows = fetch_teacher_list(supabase)
        teacher_name_to_id = {}
        for tr in teacher_rows:
            teacher_name_to_id[str(tr["name"]).strip()] = tr["id"]

        teacher_att_rows = fetch_teacher_attendance_by_range(supabase, saturday_date, sunday_date)
        present_by_day = defaultdict(set)
        for row in teacher_att_rows:
            adate = row.get("attendance_date")
            tid = row.get("teacher_id")
            if adate and tid:
                present_by_day[adate].add(tid)

        weekend_dates = {
            "sat": saturday_date.isoformat(),
            "sun": sunday_date.isoformat(),
        }
        for class_key in homeroom_map.keys():
            homeroom_name = str(homeroom_map.get(class_key) or "").strip()
            assistant_names_raw = normalize_assistant_teacher(assistant_map.get(class_key) or "")
            assistant_names = [
                name.strip() for name in assistant_names_raw.split(",") if name.strip() and name.strip() != "-"
            ]

            for day_code, adate in weekend_dates.items():
                if homeroom_name and homeroom_name != "-":
                    homeroom_tid = teacher_name_to_id.get(homeroom_name)
                    if homeroom_tid:
                        homeroom_status_map[class_key][day_code] = (
                            "present" if homeroom_tid in present_by_day.get(adate, set()) else "absent"
                        )

                if assistant_names:
                    matched_ids = [
                        teacher_name_to_id[name]
                        for name in assistant_names
                        if teacher_name_to_id.get(name)
                    ]
                    if matched_ids:
                        is_present = any(tid in present_by_day.get(adate, set()) for tid in matched_ids)
                        assistant_status_map[class_key][day_code] = "present" if is_present else "absent"
    except Exception:
        pass

    if selected_week_class[0] == "전체":
        level_keys = sorted({(s["level"], s["grade"], s["class_no"]) for s in unique_weekly_students})
        middle_keys = [k for k in level_keys if k[0] == "middle"]
        high_keys = [k for k in level_keys if k[0] == "high"]
        render_class_board(
            "중등부",
            middle_keys,
            students_by_class,
            status_by_student_day,
            homeroom_map,
            assistant_map,
            homeroom_status_map,
            assistant_status_map,
        )
        render_class_board(
            "고등부",
            high_keys,
            students_by_class,
            status_by_student_day,
            homeroom_map,
            assistant_map,
            homeroom_status_map,
            assistant_status_map,
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
            homeroom_status_map,
            assistant_status_map,
        )

    with st.expander("주간 원본(검증용)"):
        st.dataframe(weekly_display, use_container_width=True)
    return saturday_date, sunday_date


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

tab_dashboard, tab_grade, tab_class, tab_individual, tab_registration, tab_attendance = st.tabs(
    ["전체출석", "학년별출석", "반별출석", "개별출석", "신규등록", "출석인증"]
)

with tab_dashboard:
    selected_weekend = render_weekly_section(
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

        selected_weekend_dates = []
        if selected_weekend:
            selected_weekend_dates = [selected_weekend[0], selected_weekend[1]]

        weekend_counts = Counter()
        for target_day in selected_weekend_dates:
            student_map = date_student_status.get(target_day.isoformat(), {})
            day_code = day_code_from_date(target_day)
            present_cnt = sum(1 for s in student_map.values() if s == "present")
            absent_cnt = max(unique_students - present_cnt, 0)
            if day_code == "sat":
                weekend_counts["sat_present"] += present_cnt
                weekend_counts["sat_absent"] += absent_cnt
            elif day_code == "sun":
                weekend_counts["sun_present"] += present_cnt
                weekend_counts["sun_absent"] += absent_cnt

        teacher_total = 0
        teacher_weekend_counts = Counter()
        if selected_weekend_dates:
            try:
                teacher_ids = fetch_class_teacher_ids(supabase)
                teacher_total = len(teacher_ids)
                if teacher_total > 0:
                    teacher_id_set = set(teacher_ids)
                    teacher_rows = fetch_teacher_attendance_by_range(
                        supabase,
                        selected_weekend_dates[0],
                        selected_weekend_dates[-1],
                    )
                    teacher_present_by_date = defaultdict(set)
                    for row in teacher_rows:
                        tid = row.get("teacher_id")
                        adate = row.get("attendance_date")
                        if tid in teacher_id_set and adate:
                            teacher_present_by_date[adate].add(tid)

                    for target_day in selected_weekend_dates:
                        adate = target_day.isoformat()
                        day_code = day_code_from_date(target_day)
                        if day_code not in {"sat", "sun"}:
                            continue
                        present_cnt = len(teacher_present_by_date.get(adate, set()))
                        absent_cnt = max(teacher_total - present_cnt, 0)
                        if day_code == "sat":
                            teacher_weekend_counts["sat_present"] += present_cnt
                            teacher_weekend_counts["sat_absent"] += absent_cnt
                        else:
                            teacher_weekend_counts["sun_present"] += present_cnt
                            teacher_weekend_counts["sun_absent"] += absent_cnt
            except Exception:
                st.warning("선생님 출석 데이터를 불러오지 못했습니다.")

        total_cols = st.columns(5)
        total_cols[0].metric("학생 수(교사 제외)", unique_students)
        total_cols[1].metric("토요일 출석", weekend_counts.get("sat_present", 0))
        total_cols[2].metric("토요일 결석", weekend_counts.get("sat_absent", 0))
        total_cols[3].metric("일요일 출석", weekend_counts.get("sun_present", 0))
        total_cols[4].metric("일요일 결석", weekend_counts.get("sun_absent", 0))
        teacher_cols = st.columns(5)
        teacher_cols[0].metric("선생님 수(담임/부담임)", teacher_total)
        teacher_cols[1].metric("토요일 출석(선생님)", teacher_weekend_counts.get("sat_present", 0))
        teacher_cols[2].metric("토요일 결석(선생님)", teacher_weekend_counts.get("sat_absent", 0))
        teacher_cols[3].metric("일요일 출석(선생님)", teacher_weekend_counts.get("sun_present", 0))
        teacher_cols[4].metric("일요일 결석(선생님)", teacher_weekend_counts.get("sun_absent", 0))

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

        st.subheader("중등부 / 고등부 출석 인원 트렌드 (주차별)")
        week_keys = [k for k, _ in week_rows]
        week_labels = [week_label_from_sunday(date.fromisoformat(k)) for k in week_keys]
        level_student_ids = {
            "중등부": {r["student_id"] for r in class_rows if r.get("level") == "middle"},
            "고등부": {r["student_id"] for r in class_rows if r.get("level") == "high"},
        }
        level_weekly_present = defaultdict(lambda: {"중등부": {"sat": 0, "sun": 0}, "고등부": {"sat": 0, "sun": 0}})
        for adate, student_map in date_student_status.items():
            day = date.fromisoformat(adate)
            day_code = day_code_from_date(day)
            if day_code not in {"sat", "sun"}:
                continue
            week_key = (day + timedelta(days=1)).isoformat() if day_code == "sat" else day.isoformat()
            for level_name, sids in level_student_ids.items():
                level_weekly_present[week_key][level_name][day_code] = sum(
                    1 for sid, stt in student_map.items() if sid in sids and stt == "present"
                )

        middle_sat_vals = [level_weekly_present[k]["중등부"]["sat"] for k in week_keys]
        middle_sun_vals = [level_weekly_present[k]["중등부"]["sun"] for k in week_keys]
        high_sat_vals = [level_weekly_present[k]["고등부"]["sat"] for k in week_keys]
        high_sun_vals = [level_weekly_present[k]["고등부"]["sun"] for k in week_keys]
        max_level_val = max(middle_sat_vals + middle_sun_vals + high_sat_vals + high_sun_vals + [1]) * 1.35

        level_chart_cols = st.columns(2)
        with level_chart_cols[0]:
            middle_fig = go.Figure()
            middle_fig.add_trace(
                go.Scatter(
                    name="토요일",
                    x=week_labels,
                    y=middle_sat_vals,
                    mode="lines+markers+text",
                    text=middle_sat_vals,
                    textposition="top center",
                    line=dict(color="#22c55e", width=3),
                    marker=dict(size=8),
                )
            )
            middle_fig.add_trace(
                go.Scatter(
                    name="일요일",
                    x=week_labels,
                    y=middle_sun_vals,
                    mode="lines+markers+text",
                    text=middle_sun_vals,
                    textposition="top center",
                    line=dict(color="#f97316", width=3),
                    marker=dict(size=8),
                )
            )
            middle_fig.update_layout(
                title="중등부 출석 트렌드",
                yaxis=dict(title="출석 인원(명)", range=[0, max_level_val]),
                xaxis=dict(title="주차"),
                margin=dict(l=20, r=20, t=40, b=20),
                legend=dict(title="요일"),
                template="plotly_dark",
                height=300,
            )
            st.plotly_chart(middle_fig, use_container_width=True, config={"displayModeBar": False})

        with level_chart_cols[1]:
            high_fig = go.Figure()
            high_fig.add_trace(
                go.Scatter(
                    name="토요일",
                    x=week_labels,
                    y=high_sat_vals,
                    mode="lines+markers+text",
                    text=high_sat_vals,
                    textposition="top center",
                    line=dict(color="#22c55e", width=3),
                    marker=dict(size=8),
                )
            )
            high_fig.add_trace(
                go.Scatter(
                    name="일요일",
                    x=week_labels,
                    y=high_sun_vals,
                    mode="lines+markers+text",
                    text=high_sun_vals,
                    textposition="top center",
                    line=dict(color="#f97316", width=3),
                    marker=dict(size=8),
                )
            )
            high_fig.update_layout(
                title="고등부 출석 트렌드",
                yaxis=dict(title="출석 인원(명)", range=[0, max_level_val]),
                xaxis=dict(title="주차"),
                margin=dict(l=20, r=20, t=40, b=20),
                legend=dict(title="요일"),
                template="plotly_dark",
                height=300,
            )
            st.plotly_chart(high_fig, use_container_width=True, config={"displayModeBar": False})

        sibling_weekly = defaultdict(
            lambda: {
                "중등부": {"형제": {"sat": 0, "sun": 0}, "자매": {"sat": 0, "sun": 0}},
                "고등부": {"형제": {"sat": 0, "sun": 0}, "자매": {"sat": 0, "sun": 0}},
            }
        )
        for adate, student_map in date_student_status.items():
            day = date.fromisoformat(adate)
            day_code = day_code_from_date(day)
            if day_code not in {"sat", "sun"}:
                continue
            week_key = (day + timedelta(days=1)).isoformat() if day_code == "sat" else day.isoformat()
            for sid, stt in student_map.items():
                if stt != "present":
                    continue
                meta = student_group_map.get(sid, {})
                level_name = meta.get("level", "")
                sibling = meta.get("sibling", "")
                if level_name in {"중등부", "고등부"} and sibling in {"형제", "자매"}:
                    sibling_weekly[week_key][level_name][sibling][day_code] += 1

        trend_chart_rows = [st.columns(2), st.columns(2)]
        sibling_max = 1
        for wk in week_keys:
            for lv in ["중등부", "고등부"]:
                for sg in ["형제", "자매"]:
                    sibling_max = max(
                        sibling_max,
                        sibling_weekly[wk][lv][sg]["sat"],
                        sibling_weekly[wk][lv][sg]["sun"],
                    )

        def render_single_sibling_trend(title: str, level_name: str, sibling_name: str):
            sat_vals = [sibling_weekly[k][level_name][sibling_name]["sat"] for k in week_keys]
            sun_vals = [sibling_weekly[k][level_name][sibling_name]["sun"] for k in week_keys]
            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    name="토요일",
                    x=week_labels,
                    y=sat_vals,
                    mode="lines+markers+text",
                    text=sat_vals,
                    textposition="top center",
                    line=dict(color="#22c55e", width=3),
                    marker=dict(size=8),
                )
            )
            fig.add_trace(
                go.Scatter(
                    name="일요일",
                    x=week_labels,
                    y=sun_vals,
                    mode="lines+markers+text",
                    text=sun_vals,
                    textposition="top center",
                    line=dict(color="#f97316", width=3),
                    marker=dict(size=8),
                )
            )
            fig.update_layout(
                title=title,
                yaxis=dict(title="출석 인원(명)", range=[0, sibling_max * 1.35]),
                xaxis=dict(title="주차"),
                margin=dict(l=20, r=20, t=40, b=20),
                legend=dict(title="요일"),
                template="plotly_dark",
                height=300,
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        with trend_chart_rows[0][0]:
            render_single_sibling_trend("중등부 형제 트렌드", "중등부", "형제")
        with trend_chart_rows[0][1]:
            render_single_sibling_trend("중등부 자매 트렌드", "중등부", "자매")
        with trend_chart_rows[1][0]:
            render_single_sibling_trend("고등부 형제 트렌드", "고등부", "형제")
        with trend_chart_rows[1][1]:
            render_single_sibling_trend("고등부 자매 트렌드", "고등부", "자매")

with tab_individual:
    st.subheader("개별출석")
    individual_class = st.selectbox(
        "반 선택",
        class_options,
        format_func=lambda c: f"{c[0]} {c[1]}학년 {c[2]}반",
        key="individual_class_select",
    )
    individual_students = sorted(
        [r for r in class_rows if (r["level"], r["grade"], r["class_no"]) == individual_class],
        key=lambda r: (r.get("student_name") or ""),
    )
    if not individual_students:
        st.info("선택한 반에 학생 정보가 없습니다.")
    else:
        individual_student_options = {
            f"{r['student_name']} ({r['student_id'][:8]})": r for r in individual_students
        }
        selected_individual_student_label = st.selectbox(
            "학생 선택",
            list(individual_student_options.keys()),
            key="individual_student_select",
        )
        selected_student = individual_student_options[selected_individual_student_label]
        selected_student_id = selected_student["student_id"]
        selected_student_name = selected_student["student_name"]

        weekend_dates = sorted(
            {
                date.fromisoformat(r["attendance_date"])
                for r in all_rows
                if r.get("attendance_date")
                and day_code_from_date(date.fromisoformat(r["attendance_date"])) in {"sat", "sun"}
            }
        )
        if not weekend_dates:
            st.info("주말 출석 데이터가 없습니다.")
        else:
            all_week_keys = sorted(
                {
                    (d + timedelta(days=1)).isoformat() if day_code_from_date(d) == "sat" else d.isoformat()
                    for d in weekend_dates
                }
            )

            student_weekly_presence = defaultdict(lambda: {"sat": 0, "sun": 0})
            for row in all_rows:
                if row.get("student_id") != selected_student_id:
                    continue
                adate_raw = row.get("attendance_date")
                if not adate_raw:
                    continue
                adate = date.fromisoformat(adate_raw)
                day_code = day_code_from_date(adate)
                if day_code not in {"sat", "sun"}:
                    continue
                if normalize_status(row.get("status")) != "present":
                    continue
                week_key = (adate + timedelta(days=1)).isoformat() if day_code == "sat" else adate.isoformat()
                student_weekly_presence[week_key][day_code] = 1

            week_labels = [week_label_from_sunday(date.fromisoformat(k)) for k in all_week_keys]
            sat_vals = [student_weekly_presence[k]["sat"] for k in all_week_keys]
            sun_vals = [student_weekly_presence[k]["sun"] for k in all_week_keys]
            sat_marker_colors, sun_marker_colors = attendance_pair_marker_colors(
                sat_vals, sun_vals, 1, "#22c55e", "#f97316"
            )

            summary_cols = st.columns(2)
            summary_cols[0].metric("토요일 출석 횟수", sum(sat_vals))
            summary_cols[1].metric("일요일 출석 횟수", sum(sun_vals))
            render_attendance_color_guide()

            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    name="토요일",
                    x=week_labels,
                    y=sat_vals,
                    mode="lines+markers+text",
                    text=sat_vals,
                    textposition="top center",
                    line=dict(color="#22c55e", width=3),
                    marker=dict(size=8, color=sat_marker_colors),
                )
            )
            fig.add_trace(
                go.Scatter(
                    name="일요일",
                    x=week_labels,
                    y=sun_vals,
                    mode="lines+markers+text",
                    text=sun_vals,
                    textposition="top center",
                    line=dict(color="#f97316", width=3),
                    marker=dict(size=8, color=sun_marker_colors),
                )
            )
            fig.update_layout(
                title=f"{selected_student_name} 주차별 출석 트렌드",
                yaxis=dict(
                    title="출석 여부",
                    range=[-0.05, 1.15],
                    tickmode="array",
                    tickvals=[0, 1],
                    ticktext=["결석", "출석"],
                ),
                xaxis=dict(title="주차"),
                margin=dict(l=20, r=20, t=60, b=20),
                legend=dict(title="요일"),
                template="plotly_dark",
                height=380,
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

with tab_grade:
    st.subheader("학년별출석")
    grade_options = sorted(
        {(r["level"], int(r["grade"])) for r in class_rows if r.get("level") and r.get("grade")},
        key=lambda x: (level_order.get(x[0], 99), x[1]),
    )
    if not grade_options:
        st.info("학년 정보가 없습니다.")
    else:
        selected_grade = st.selectbox(
            "학년 선택",
            grade_options,
            format_func=lambda g: f"{'중등부' if g[0] == 'middle' else '고등부'} {g[1]}학년",
            key="grade_attendance_target_select",
        )
        grade_students = [
            r for r in class_rows if r["level"] == selected_grade[0] and int(r["grade"]) == selected_grade[1]
        ]
        grade_student_ids = {r["student_id"] for r in grade_students if r.get("student_id")}
        grade_size = len(grade_student_ids)
        grade_label = f"{'중등부' if selected_grade[0] == 'middle' else '고등부'} {selected_grade[1]}학년"

        if grade_size == 0:
            st.info("선택한 학년의 학생 정보가 없습니다.")
        else:
            weekend_dates = sorted(
                {
                    date.fromisoformat(r["attendance_date"])
                    for r in all_rows
                    if r.get("attendance_date")
                    and day_code_from_date(date.fromisoformat(r["attendance_date"])) in {"sat", "sun"}
                }
            )
            if not weekend_dates:
                st.info("주말 출석 데이터가 없습니다.")
            else:
                all_week_keys = sorted(
                    {
                        (d + timedelta(days=1)).isoformat() if day_code_from_date(d) == "sat" else d.isoformat()
                        for d in weekend_dates
                    }
                )

                grade_weekly_presence = defaultdict(lambda: {"sat": 0, "sun": 0})
                for row in all_rows:
                    sid = row.get("student_id")
                    if sid not in grade_student_ids:
                        continue
                    adate_raw = row.get("attendance_date")
                    if not adate_raw:
                        continue
                    adate = date.fromisoformat(adate_raw)
                    day_code = day_code_from_date(adate)
                    if day_code not in {"sat", "sun"}:
                        continue
                    if normalize_status(row.get("status")) != "present":
                        continue
                    week_key = (
                        (adate + timedelta(days=1)).isoformat() if day_code == "sat" else adate.isoformat()
                    )
                    grade_weekly_presence[week_key][day_code] += 1

                week_labels = [week_label_from_sunday(date.fromisoformat(k)) for k in all_week_keys]
                sat_vals = [grade_weekly_presence[k]["sat"] for k in all_week_keys]
                sun_vals = [grade_weekly_presence[k]["sun"] for k in all_week_keys]
                sat_marker_colors, sun_marker_colors = attendance_pair_marker_colors(
                    sat_vals, sun_vals, grade_size, "#22c55e", "#f97316"
                )

                summary_cols = st.columns(3)
                summary_cols[0].metric("학년 인원", grade_size)
                summary_cols[1].metric("토요일 총 출석", sum(sat_vals))
                summary_cols[2].metric("일요일 총 출석", sum(sun_vals))
                render_attendance_color_guide()

                y_max = max(sat_vals + sun_vals + [grade_size, 1]) * 1.15
                fig = go.Figure()
                fig.add_trace(
                    go.Scatter(
                        name="토요일",
                        x=week_labels,
                        y=sat_vals,
                        mode="lines+markers+text",
                        text=sat_vals,
                        textposition="top center",
                        line=dict(color="#22c55e", width=3),
                        marker=dict(size=8, color=sat_marker_colors),
                    )
                )
                fig.add_trace(
                    go.Scatter(
                        name="일요일",
                        x=week_labels,
                        y=sun_vals,
                        mode="lines+markers+text",
                        text=sun_vals,
                        textposition="top center",
                        line=dict(color="#f97316", width=3),
                        marker=dict(size=8, color=sun_marker_colors),
                    )
                )
                fig.update_layout(
                    title=f"{grade_label} 주차별 출석 트렌드",
                    yaxis=dict(title="출석 인원(명)", range=[0, y_max]),
                    xaxis=dict(title="주차"),
                    margin=dict(l=20, r=20, t=60, b=20),
                    legend=dict(title="요일"),
                    template="plotly_dark",
                    height=380,
                )
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

with tab_class:
    st.subheader("반별출석")
    class_attendance_target = st.selectbox(
        "반 선택",
        class_options,
        format_func=lambda c: f"{c[0]} {c[1]}학년 {c[2]}반",
        key="class_attendance_target_select",
    )
    class_students = [
        r
        for r in class_rows
        if (r["level"], r["grade"], r["class_no"]) == class_attendance_target and r.get("student_id")
    ]
    class_student_ids = {r["student_id"] for r in class_students}
    class_size = len(class_student_ids)
    class_label = f"{class_attendance_target[0]} {class_attendance_target[1]}학년 {class_attendance_target[2]}반"

    if class_size == 0:
        st.info("선택한 반의 학생 정보가 없습니다.")
    else:
        weekend_dates = sorted(
            {
                date.fromisoformat(r["attendance_date"])
                for r in all_rows
                if r.get("attendance_date")
                and day_code_from_date(date.fromisoformat(r["attendance_date"])) in {"sat", "sun"}
            }
        )
        if not weekend_dates:
            st.info("주말 출석 데이터가 없습니다.")
        else:
            all_week_keys = sorted(
                {
                    (d + timedelta(days=1)).isoformat() if day_code_from_date(d) == "sat" else d.isoformat()
                    for d in weekend_dates
                }
            )

            class_weekly_presence = defaultdict(lambda: {"sat": 0, "sun": 0})
            for row in all_rows:
                sid = row.get("student_id")
                if sid not in class_student_ids:
                    continue
                adate_raw = row.get("attendance_date")
                if not adate_raw:
                    continue
                adate = date.fromisoformat(adate_raw)
                day_code = day_code_from_date(adate)
                if day_code not in {"sat", "sun"}:
                    continue
                if normalize_status(row.get("status")) != "present":
                    continue
                week_key = (adate + timedelta(days=1)).isoformat() if day_code == "sat" else adate.isoformat()
                class_weekly_presence[week_key][day_code] += 1

            week_labels = [week_label_from_sunday(date.fromisoformat(k)) for k in all_week_keys]
            sat_vals = [class_weekly_presence[k]["sat"] for k in all_week_keys]
            sun_vals = [class_weekly_presence[k]["sun"] for k in all_week_keys]
            sat_marker_colors, sun_marker_colors = attendance_pair_marker_colors(
                sat_vals, sun_vals, class_size, "#22c55e", "#f97316"
            )

            summary_cols = st.columns(3)
            summary_cols[0].metric("반 인원", class_size)
            summary_cols[1].metric("토요일 총 출석", sum(sat_vals))
            summary_cols[2].metric("일요일 총 출석", sum(sun_vals))
            render_attendance_color_guide()

            y_max = max(sat_vals + sun_vals + [class_size, 1]) * 1.15
            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    name="토요일",
                    x=week_labels,
                    y=sat_vals,
                    mode="lines+markers+text",
                    text=sat_vals,
                    textposition="top center",
                    line=dict(color="#22c55e", width=3),
                    marker=dict(size=8, color=sat_marker_colors),
                )
            )
            fig.add_trace(
                go.Scatter(
                    name="일요일",
                    x=week_labels,
                    y=sun_vals,
                    mode="lines+markers+text",
                    text=sun_vals,
                    textposition="top center",
                    line=dict(color="#f97316", width=3),
                    marker=dict(size=8, color=sun_marker_colors),
                )
            )
            fig.update_layout(
                title=f"{class_label} 주차별 출석 트렌드",
                yaxis=dict(title="출석 인원(명)", range=[0, y_max]),
                xaxis=dict(title="주차"),
                margin=dict(l=20, r=20, t=60, b=20),
                legend=dict(title="요일"),
                template="plotly_dark",
                height=380,
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

with tab_registration:
    st.markdown("#### 학생 추가")
    add_student_cols = st.columns([3, 3, 1])
    with add_student_cols[0]:
        new_student_name = st.text_input("학생 이름", key="admin_new_student_name")
    with add_student_cols[1]:
        new_student_class = st.selectbox(
            "배정 반",
            class_options,
            format_func=lambda c: f"{c[0]} {c[1]}학년 {c[2]}반",
            key="admin_new_student_class",
        )
    with add_student_cols[2]:
        st.markdown("<div style='height: 1.8rem'></div>", unsafe_allow_html=True)
        submit_new_student = st.button("학생 추가", use_container_width=True, key="admin_new_student_submit")

    if submit_new_student:
        if not new_student_name.strip():
            st.warning("학생 이름을 입력하세요.")
        else:
            level, grade, class_no = new_student_class
            try:
                school_class_id = find_school_class_id(supabase, level, int(grade), int(class_no))
                if not school_class_id:
                    st.error("school_class에서 선택한 반을 찾지 못했습니다.")
                else:
                    create_student_with_class(
                        client=supabase,
                        student_name=new_student_name,
                        school_class_id=school_class_id,
                    )
                    st.success(f"학생 추가 완료: {new_student_name} ({level} {grade}학년 {class_no}반)")
                    st.cache_data.clear()
                    st.rerun()
            except Exception as e:
                st.error(f"학생 추가 실패: {e}")

    st.markdown("#### 학생 삭제")
    delete_student_cols = st.columns([2, 3, 1])
    with delete_student_cols[0]:
        delete_student_class = st.selectbox(
            "삭제할 반",
            class_options,
            format_func=lambda c: f"{c[0]} {c[1]}학년 {c[2]}반",
            key="admin_delete_student_class",
        )
    students_in_delete_class = [
        r for r in class_rows if (r["level"], r["grade"], r["class_no"]) == delete_student_class
    ]
    delete_student_options = {
        f"{r['student_name']} ({r['student_id'][:8]})": r for r in students_in_delete_class
    }
    with delete_student_cols[1]:
        if delete_student_options:
            selected_delete_student_label = st.selectbox(
                "삭제할 학생",
                list(delete_student_options.keys()),
                key="admin_delete_student_pick",
            )
        else:
            selected_delete_student_label = ""
            st.info("선택한 반에 삭제할 학생이 없습니다.")
    with delete_student_cols[2]:
        st.markdown("<div style='height: 1.8rem'></div>", unsafe_allow_html=True)
        submit_delete_student = st.button(
            "학생 삭제",
            use_container_width=True,
            key="admin_delete_student_submit",
            disabled=not bool(delete_student_options),
        )

    if submit_delete_student and delete_student_options:
        target_student = delete_student_options[selected_delete_student_label]
        try:
            delete_student_with_related(supabase, target_student["student_id"])
            st.success(f"학생 삭제 완료: {target_student['student_name']}")
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(f"학생 삭제 실패: {e}")

with tab_attendance:
    manual_flash_success = st.session_state.pop("manual_flash_success", "")
    if manual_flash_success:
        st.success(manual_flash_success)
    manual_flash_error = st.session_state.pop("manual_flash_error", "")
    if manual_flash_error:
        st.error(manual_flash_error)

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
        selected_manual_students = st.multiselect(
            "학생(복수 선택 가능)",
            list(manual_student_options.keys()),
            key="manual_student_multi_pick",
        )
        manual_action_cols = st.columns([1, 3])
        with manual_action_cols[0]:
            submit_manual = st.button("수동 출석 등록", use_container_width=True, key="manual_submit")

        if submit_manual:
            if day_code_from_date(manual_date) not in {"sat", "sun"}:
                st.warning("수동 출석 등록은 토요일/일요일만 가능합니다.")
            elif not selected_manual_students:
                st.warning("출석 등록할 학생을 1명 이상 선택하세요.")
            else:
                success_names = []
                failed_names = []
                for selected_label in selected_manual_students:
                    student = manual_student_options[selected_label]
                    school_class_id = find_school_class_id_by_student_id(supabase, student["student_id"])
                    if not school_class_id:
                        failed_names.append(student["student_name"])
                        continue
                    try:
                        save_attendance(
                            client=supabase,
                            attendance_date=manual_date,
                            student_id=student["student_id"],
                            school_class_id=school_class_id,
                            status="present",
                            note="manual check-in",
                        )
                        success_names.append(student["student_name"])
                    except Exception:
                        failed_names.append(student["student_name"])

                if success_names:
                    st.session_state["manual_flash_success"] = (
                        f"수동 등록 완료({len(success_names)}명): {', '.join(success_names)}"
                    )
                    if failed_names:
                        st.session_state["manual_flash_error"] = f"수동 등록 실패: {', '.join(failed_names)}"
                    st.cache_data.clear()
                    st.rerun()
                elif failed_names:
                    st.error(f"수동 등록 실패: {', '.join(failed_names)}")

        st.markdown("##### 수동 출석 취소(삭제)")
        cancel_date = st.date_input(
            "취소할 날짜",
            value=manual_date,
            key="manual_cancel_date_input",
        )
        st.caption(f"취소 날짜 요일: {day_label_from_date(cancel_date)}")
        cancel_class = st.selectbox(
            "취소할 반",
            class_options,
            format_func=lambda c: f"{c[0]} {c[1]}학년 {c[2]}반",
            key="manual_cancel_class_select",
        )
        cancel_students = [
            r for r in class_rows if (r["level"], r["grade"], r["class_no"]) == cancel_class
        ]
        cancel_student_options = {
            f"{r['student_name']} ({r['student_id'][:8]})": r for r in cancel_students
        }
        if cancel_student_options:
            cancel_student_label = st.selectbox(
                "취소할 학생",
                list(cancel_student_options.keys()),
                key="manual_cancel_student_pick",
            )
        else:
            cancel_student_label = ""
            st.info("선택한 취소 반에 학생 정보가 없습니다.")
        cancel_action_cols = st.columns([1, 3])
        with cancel_action_cols[0]:
            cancel_manual = st.button(
                "출석 취소",
                use_container_width=True,
                key="manual_cancel_submit",
                disabled=not bool(cancel_student_options),
            )

        if cancel_manual:
            if day_code_from_date(cancel_date) not in {"sat", "sun"}:
                st.warning("수동 출석 취소는 토요일/일요일만 가능합니다.")
            else:
                student = cancel_student_options[cancel_student_label]
                try:
                    delete_attendance(
                        client=supabase,
                        attendance_date=cancel_date,
                        student_id=student["student_id"],
                    )
                    st.success(f"수동 출석 취소 완료: {student['student_name']}")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"수동 출석 취소 실패: {e}")

        st.markdown("##### 선생님 출석 취소")
        teacher_cancel_date = st.date_input(
            "선생님 취소 날짜",
            value=manual_date,
            key="manual_teacher_cancel_date_input",
        )
        st.caption(f"선생님 취소 날짜 요일: {day_label_from_date(teacher_cancel_date)}")
        teacher_cancel_options = {}
        try:
            teacher_rows = fetch_teacher_list(supabase)
            assigned_teacher_ids = set(fetch_class_teacher_ids(supabase))
            assigned_teachers = [t for t in teacher_rows if t.get("id") in assigned_teacher_ids]
            teacher_cancel_options = {
                f"{t['name']} ({t['id'][:8]})": t for t in sorted(assigned_teachers, key=lambda x: x["name"])
            }
        except Exception:
            teacher_cancel_options = {}

        if teacher_cancel_options:
            teacher_cancel_label = st.selectbox(
                "취소할 선생님",
                list(teacher_cancel_options.keys()),
                key="manual_teacher_cancel_pick",
            )
        else:
            teacher_cancel_label = ""
            st.info("취소 가능한 선생님 목록을 불러오지 못했습니다.")

        teacher_cancel_cols = st.columns([1, 3])
        with teacher_cancel_cols[0]:
            submit_teacher_cancel = st.button(
                "선생님 출석 취소",
                use_container_width=True,
                key="manual_teacher_cancel_submit",
                disabled=not bool(teacher_cancel_options),
            )

        if submit_teacher_cancel:
            if day_code_from_date(teacher_cancel_date) not in {"sat", "sun"}:
                st.warning("선생님 출석 취소는 토요일/일요일만 가능합니다.")
            else:
                teacher = teacher_cancel_options[teacher_cancel_label]
                try:
                    delete_teacher_attendance(
                        client=supabase,
                        attendance_date=teacher_cancel_date,
                        teacher_id=teacher["id"],
                    )
                    st.success(f"선생님 출석 취소 완료: {teacher['name']}")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"선생님 출석 취소 실패: {e}")

    st.markdown("#### QR 출석 링크 생성")
    qr_cols = st.columns(3)
    with qr_cols[0]:
        qr_date = st.date_input("QR 날짜", value=date.today(), key="qr_date_input")
    with qr_cols[1]:
        qr_target_label = st.selectbox(
            "QR 대상",
            ["학생", "선생님"],
            index=0,
            key="qr_target_select",
        )
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
        qr_target = "teacher" if qr_target_label == "선생님" else "student"
        qr_url = build_qr_checkin_url(
            base_url=app_base_url,
            attendance_date=qr_date,
            status="present",
            qr_slot=active_qr_slot,
            target=qr_target,
        )
        st.caption(
            f"현재 QR 유효시간: {slot_start.strftime('%H:%M')} ~ {slot_end.strftime('%H:%M')} ({app_tz})"
        )
        st.code(qr_url)
        if app_base_url:
            qr_caption = (
                "선생님이 스캔한 뒤 이름 입력으로 teacher_attendance에 출석 처리됩니다."
                if qr_target == "teacher"
                else "학생이 스캔한 뒤 이름 입력 + source_key 선택으로 출석 처리됩니다."
            )
            st.image(
                f"https://quickchart.io/qr?size=170&text={quote_plus(qr_url)}",
                caption=qr_caption,
                width=170,
            )
        else:
            st.warning(
                "앱 URL을 자동으로 찾지 못했습니다. Streamlit Secrets에 "
                "`APP_BASE_URL = \"https://<app>.streamlit.app\"`를 추가하세요."
            )
