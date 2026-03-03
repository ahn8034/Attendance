import os
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


def save_attendance(
    client: Client,
    attendance_date: date,
    attendee_name: str,
    status: str,
    note: str,
    created_by: str,
) -> None:
    payload = {
        "attendance_date": attendance_date.isoformat(),
        "attendee_name": attendee_name.strip(),
        "status": status,
        "note": note.strip() if note else None,
        "created_by": created_by.strip() if created_by else None,
    }

    (
        client.table("attendance")
        .upsert(payload, on_conflict="attendance_date,attendee_name")
        .execute()
    )


def fetch_attendance(client: Client, attendance_date: date):
    result = (
        client.table("attendance")
        .select("id, attendance_date, attendee_name, status, note, created_by, created_at")
        .eq("attendance_date", attendance_date.isoformat())
        .order("attendee_name")
        .execute()
    )
    return result.data or []


st.title("출석부 앱")
st.caption("Streamlit + Supabase")

try:
    supabase = get_supabase()
except Exception as e:
    st.error(str(e))
    st.stop()

col1, col2 = st.columns([1, 2])
with col1:
    selected_date = st.date_input("출석 날짜", value=date.today())
with col2:
    manager_name = st.text_input("작성자", placeholder="예: 담임선생님")

with st.form("attendance_form", clear_on_submit=True):
    form_cols = st.columns([2, 1, 2])
    with form_cols[0]:
        attendee_name = st.text_input("이름", placeholder="예: 김민수")
    with form_cols[1]:
        status = st.selectbox("상태", ["present", "late", "absent"], index=0)
    with form_cols[2]:
        note = st.text_input("비고", placeholder="선택")

    submitted = st.form_submit_button("저장")

if submitted:
    if not attendee_name.strip():
        st.warning("이름을 입력하세요.")
    else:
        try:
            save_attendance(
                client=supabase,
                attendance_date=selected_date,
                attendee_name=attendee_name,
                status=status,
                note=note,
                created_by=manager_name,
            )
            st.success(f"저장 완료: {attendee_name} ({status})")
        except Exception as e:
            st.error(f"저장 실패: {e}")

st.divider()
st.subheader(f"{selected_date} 출석 현황")

try:
    rows = fetch_attendance(supabase, selected_date)
except Exception as e:
    st.error(f"조회 실패: {e}")
    st.stop()

if not rows:
    st.info("해당 날짜의 출석 데이터가 없습니다.")
else:
    summary_cols = st.columns(3)
    present_cnt = sum(1 for r in rows if r["status"] == "present")
    late_cnt = sum(1 for r in rows if r["status"] == "late")
    absent_cnt = sum(1 for r in rows if r["status"] == "absent")

    summary_cols[0].metric("출석", present_cnt)
    summary_cols[1].metric("지각", late_cnt)
    summary_cols[2].metric("결석", absent_cnt)

    st.dataframe(rows, use_container_width=True)
