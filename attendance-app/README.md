# Attendance App (Streamlit + Supabase)

간단한 출석 입력/조회 앱입니다.

## 1) 설치

```bash
cd attendance-app
pip install -r requirements.txt
```

## 2) Supabase 설정

1. Supabase SQL Editor에서 `sql/schema.sql` 실행
2. `.streamlit/secrets.toml` 파일 생성

```toml
SUPABASE_URL = "https://pteblbjrggqvhuwltcnj.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_..."
```

## 3) 실행

```bash
streamlit run app.py
```

## 참고

- 현재 정책은 데모용으로 `anon` 입력/조회/수정을 허용합니다.
- 실제 운영 시에는 Auth 기반 RLS 정책으로 바꾸세요.
