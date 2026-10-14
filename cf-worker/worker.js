// 머니파이 데일리 리포트 예약 실행 (Cloudflare Worker, 2026-10-14)
// cron(UTC): "31 6 * * MON-FRI" = 평일 15:31 KST 국장 / "10 22 * * MON-FRI" = 화~토 07:10 KST 미장
// 주의: Cloudflare 요일 숫자는 1 = 일요일(1-5 = 일~목). 숫자 대신 MON-FRI를 쓸 것
// 할 일: GitHub Actions daily.yml을 workflow_dispatch로 실행 요청. 실패하면 한 번 더, 그래도 실패면 텔레그램 알림.
// 비밀값(Settings → Variables and Secrets, 종류 Secret):
//   GH_TOKEN          GitHub 세분화 토큰(moneypier 저장소만, Actions 읽기·쓰기)
//   TELEGRAM_TOKEN    실패 알림용 봇 토큰(없으면 알림 생략)
//   TELEGRAM_CHAT_ID  547869092

const REPO = "kangsaem/moneypier";
const WORKFLOW = "daily.yml";
const REF = "main";

async function dispatch(env) {
  const r = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "moneypie-cron",
      },
      body: JSON.stringify({ ref: REF }),
    }
  );
  if (r.status === 204) return null; // 성공
  return `HTTP ${r.status} ${(await r.text()).slice(0, 200)}`;
}

async function notify(env, text) {
  if (!env.TELEGRAM_TOKEN || !env.TELEGRAM_CHAT_ID) return;
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text }),
  });
}

async function run(env, label) {
  let err = await dispatch(env).catch((e) => String(e));
  if (err) {
    await new Promise((s) => setTimeout(s, 20000)); // 20초 뒤 한 번 더
    err = await dispatch(env).catch((e) => String(e));
  }
  if (err) await notify(env, `[머니파이] ${label} 리포트 실행 요청 실패\n${err}\nGitHub Actions에서 수동 실행 필요`);
  console.log(label, err || "실행 요청 성공");
  return err;
}

export default {
  async scheduled(event, env, ctx) {
    const label = event.cron.startsWith("31 6") ? "국장" : "미장";
    ctx.waitUntil(run(env, label));
  },
  // 브라우저로 주소를 열면 상태만 보여 줌(실행하지 않음)
  async fetch() {
    return new Response("moneypie-cron: 평일 15:31(국장) · 화~토 07:10(미장) KST에 리포트 실행 요청", {
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  },
};
