import os
from collections import Counter, defaultdict
from datetime import date

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


st.title("출석부 앱")
st.caption("Streamlit + Supabase")

try:
    supabase = get_supabase()
except Exception as e:
    st.error(str(e))
    st.stop()

try:
    all_rows = fetch_all_roster(supabase)
    class_rows = fetch_class_detail(supabase)
    class_id_map = fetch_school_class_map(supabase)
except Exception as e:
    st.error(f"초기 데이터 조회 실패: {e}")
    st.stop()

st.subheader("전체 출석 데이터")
if not all_rows:
    st.info("저장된 출석 데이터가 없습니다.")
else:
    status_counts = Counter(row["status"] for row in all_rows)
    unique_students = len({row["student_id"] for row in all_rows})
    date_counts = defaultdict(int)
    for row in all_rows:
        date_counts[row["attendance_date"]] += 1

    metric_cols = st.columns(4)
    metric_cols[0].metric("전체 기록", len(all_rows))
    metric_cols[1].metric("학생 수", unique_students)
    metric_cols[2].metric("출석", status_counts.get("present", 0))
    metric_cols[3].metric("결석", status_counts.get("absent", 0))

    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        st.caption("상태별 분포")
        st.bar_chart(
            {
                "count": {
                    "present": status_counts.get("present", 0),
                    "late": status_counts.get("late", 0),
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

class_options = sorted({(r["level"], r["grade"], r["class_no"]) for r in class_rows})
if not class_options:
    st.warning("반 정보가 없습니다. v_class_detail 데이터를 확인하세요.")
    st.stop()

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
    status = st.selectbox("상태", ["present", "late", "absent"], index=0)
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
    summary_cols = st.columns(3)
    present_cnt = sum(1 for r in filtered_rows if r["status"] == "present")
    late_cnt = sum(1 for r in filtered_rows if r["status"] == "late")
    absent_cnt = sum(1 for r in filtered_rows if r["status"] == "absent")

    summary_cols[0].metric("출석", present_cnt)
    summary_cols[1].metric("지각", late_cnt)
    summary_cols[2].metric("결석", absent_cnt)

    st.dataframe(filtered_rows, use_container_width=True)
