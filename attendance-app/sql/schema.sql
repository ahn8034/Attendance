create table if not exists public.attendance (
  id uuid primary key default gen_random_uuid(),
  attendance_date date not null,
  attendee_name text not null,
  status text not null check (status in ('present', 'late', 'absent')),
  note text,
  created_by text,
  created_at timestamptz not null default now(),
  unique (attendance_date, attendee_name)
);

alter table public.attendance enable row level security;

-- 데모용: anon 사용자에게 조회/입력/수정 허용
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'attendance' AND policyname = 'anon can read attendance'
  ) THEN
    CREATE POLICY "anon can read attendance"
      ON public.attendance
      FOR SELECT
      TO anon
      USING (true);
  END IF;
END
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'attendance' AND policyname = 'anon can insert attendance'
  ) THEN
    CREATE POLICY "anon can insert attendance"
      ON public.attendance
      FOR INSERT
      TO anon
      WITH CHECK (true);
  END IF;
END
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'attendance' AND policyname = 'anon can update attendance'
  ) THEN
    CREATE POLICY "anon can update attendance"
      ON public.attendance
      FOR UPDATE
      TO anon
      USING (true)
      WITH CHECK (true);
  END IF;
END
$$;
