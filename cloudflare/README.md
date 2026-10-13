# Cloudflare Worker 예약 설정 (daily.yml 정시 실행)

GitHub 자체 예약이 몇 시간씩 늦게 돌아서(10-12 15:31분이 00:50에, 10-13 07:10분이 12:00에),
Cloudflare Worker가 정해진 시각에 `daily.yml`을 직접 실행하게 한다. Worker는 "실행해"라는 호출만 하고,
종가 받기·신호 계산·기록은 그대로 GitHub Actions가 한다. Cloudflare 무료 요금제로 충분하다.

## 1. GitHub 토큰 만들기 (휴대폰·PC 어디서든)
1. github.com → 오른쪽 위 프로필 → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token
2. Token name: `moneypier-cron` · Expiration: 1년(만료일은 HANDOFF에 적어 둘 것)
3. Repository access: **Only select repositories** → `kangsaem/moneypier`
4. Permissions → Repository permissions → **Actions: Read and write** (나머지는 건드리지 않음. Metadata 읽기는 자동)
5. Generate → 나온 토큰(`github_pat_…`)을 복사(이 화면에서만 보임)

## 2. Worker 만들기 (Cloudflare 대시보드)
1. dash.cloudflare.com → Workers & Pages → Create → **Create Worker** → 이름 `moneypier-cron` → Deploy
2. **Edit code** → 기존 코드를 지우고 이 폴더의 `worker.js` 내용 전체를 붙여넣기 → Deploy
3. Worker 화면 → Settings → **Variables and Secrets** → Add
   - Type **Secret**, Name `GH_TOKEN`, Value = 1번 토큰
   - (선택) Type **Secret**, Name `RUN_KEY`, Value = 아무 문자열(수동 시험용)
4. Settings → **Trigger Events**(또는 Triggers) → Add → **Cron Triggers**
   - `31 6 * * 1-5` (한국시간 평일 15:31)
   - `10 22 * * 1-5` (한국시간 화~토 07:10)
   - Cloudflare 예약은 UTC 기준. 위 값 그대로 넣으면 됨.

## 3. 확인
- 수동 시험(RUN_KEY를 넣었을 때): 브라우저에서 `https://moneypier-cron.<계정>.workers.dev/run?key=<RUN_KEY>`
  → `204 실행 요청 완료`가 나오고 GitHub → Actions에 daily-report 실행이 하나 생기면 성공.
  (`401`/`403`/`404` = 토큰 권한·저장소 선택 확인)
- 예약 시험: 다음 15:31 또는 07:10에 GitHub Actions에 **workflow_dispatch**로 시작된 실행이 1~2분 안에 생기는지 확인.
- 실패 기록: Worker → Logs(또는 Observability)에 `dispatch 실패`로 남음.

## 4. 확인된 뒤 할 일
- `daily.yml`의 `schedule:` 두 줄을 지운다(남기면 늦게 한 번 더 돌아 텔레그램이 두 번 오고,
  `concurrency: cancel-in-progress` 때문에 진행 중인 정시 실행이 늦은 실행에 취소될 수도 있음).
- 토큰 만료일 전에 새 토큰으로 `GH_TOKEN`만 바꾸면 됨.
