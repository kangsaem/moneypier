// 머니파이 리포트 예약 실행기 (Cloudflare Worker)
// GitHub 자체 예약(schedule)이 몇 시간씩 늦게 도는 문제 때문에, 정해진 시각에 daily.yml을 직접 실행(workflow_dispatch)한다.
// 데이터는 다루지 않음 — 종가 받기·신호 계산·기록은 전부 GitHub Actions(build_page.py)가 한다.
//
// 예약(Cron Triggers, UTC):
//   31 6 * * 1-5   = 한국시간 평일 15:31 (국장)
//   10 22 * * 1-5  = 한국시간 화~토 07:10 (미장, 서머타임 무관)
//
// 비밀값(Settings → Variables and Secrets, 종류 Secret):
//   GH_TOKEN = GitHub 세분화 토큰 (저장소 kangsaem/moneypier만, 권한 Actions: Read and write)
//   RUN_KEY  = (선택) 주소로 수동 실행할 때 쓰는 아무 문자열. 없으면 수동 실행 꺼짐.
//
// 수동 시험: https://<worker 주소>/run?key=<RUN_KEY>  → GitHub Actions에 daily-report 실행이 하나 생기면 정상.

const REPO = "kangsaem/moneypier";
const WORKFLOW = "daily.yml";
const REF = "main";

async function dispatch(env, why) {
  if (!env.GH_TOKEN) return { ok: false, status: 0, text: "GH_TOKEN 비밀값 없음" };
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GH_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "moneypier-cron-worker",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: REF }),
  });
  const text = r.status === 204 ? "실행 요청 완료" : await r.text();
  console.log(`[${why}] ${r.status} ${text}`);
  return { ok: r.status === 204, status: r.status, text };
}

export default {
  // 예약 실행
  async scheduled(event, env, ctx) {
    const res = await dispatch(env, `cron ${event.cron}`);
    if (!res.ok) throw new Error(`dispatch 실패 ${res.status}: ${res.text}`); // 대시보드 로그에 실패로 남김
  },

  // 수동 시험(선택)
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/run") return new Response("moneypier cron worker\n", { status: 200 });
    if (!env.RUN_KEY || url.searchParams.get("key") !== env.RUN_KEY) return new Response("forbidden\n", { status: 403 });
    const res = await dispatch(env, "manual");
    return new Response(`${res.status} ${res.text}\n`, { status: res.ok ? 200 : 500, headers: { "content-type": "text/plain; charset=utf-8" } });
  },
};
