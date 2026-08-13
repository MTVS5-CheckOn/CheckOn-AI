"""`.env` 를 **어디서 기동하든** 찾는다 — 작업 디렉터리에 의존하지 않는다 (99 #73).

🔴 **왜 생겼나 — 2026-08-14 브리핑 11건 사고의 「나머지 절반」이다.**

#32 의 처방(#267)은 *"`os.environ` 만 보지 말고 `.env` 를 읽어라"* 였고 그건 맞았다.
🔴 **다만 `env_file=".env"` 는 상대경로라 「읽는다」가 아니라 「**작업 디렉터리**에서 읽는다」
였다.** 저장소 루트가 아닌 데서 기동하면 파일을 **못 찾고**, pydantic-settings 는 그걸
**오류로 만들지 않는다** — 조용히 선언 기본값으로 떨어진다.

    저장소 루트에서 기동   `.env` 읽힘        ✅
    다른 데서 기동         🔴 **안 읽힘**      ⇒ `CHECKON_ALLOW_REAL_LLM` 이 `False`
                                                ⇒ 브리핑 전건 템플릿 폴백
                                                ⇒ `DATABASE_URL` 도 같이 죽는다

⚠ **증상이 사고와 똑같고 원인만 다르다.** 그래서 «#267 로 고쳤다» 는 판단이 서면
**아무도 여기를 안 본다** — 실제로 `real_llm.py`·`console.py` docstring 이
*"어떤 기동 방식으로도 산다"* 고 **적어 두고 있었다**(2026-08-14 정정).

🔴 **윈도우에서 더 잘 터진다.** 서비스 등록·작업 스케줄러·바로가기는 작업 디렉터리가
저장소 루트가 아니다(기본이 `C:\\Windows\\System32` 인 경우가 흔하다). 맥·리눅스에서
`cd` 해서 띄우는 개발자는 **평생 못 본다.**

━━ 어떻게 — **앵커 + CWD 두 겹, 뒤가 이긴다** ━━

    ENV_FILES = (저장소루트/.env, ".env")

pydantic-settings 는 `env_file` 이 시퀀스면 **뒤에 온 파일이 이긴다.** ⇒ 앵커가
「어디서 띄워도 산다」를 보장하고, CWD 쪽이 **덮어쓸 자유**를 남긴다. 실측(2026-08-14)::

    저장소 루트 기동    앵커 `1`   상대 `1`
    다른 데서 기동      앵커 `1`   상대 🔴 **안 읽힘**
    CWD 에 `.env=2`     앵커 `2`              ← CWD 가 이긴다

🔴 **CWD 가 이기는 것이 중요하다 — 안 그러면 검사가 죽는다.** 세션 핀과 여러 검사가
`monkeypatch.chdir(tmp_path)` + 임시 `.env` 로 「이 값이면 이렇게 된다」를 만든다
(`test_real_llm_optin_guard.py` 의 `_write_dotenv`·핀 축 검사). 앵커가 이기면 그 검사들이
**저장소의 진짜 `.env` 를 보게 되어 기기마다 갈린다** — 99 #57·#63 이 그 병이었다.

⚠ **우선순위 전체는 안 바뀐다** — init 인자 > 프로세스 env > `.env`(CWD > 앵커) >
선언 기본값. 셸에서 켜는 기존 사용법도, 핀의 `env_file = None` 도 그대로 산다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

#: 저장소 루트 — `src/ai/runtime/env_files.py` 기준 네 단계 위.
#: ⚠ `pre_pr_verify.py`·계약 검사들이 쓰는 `parents[3]` 과 같은 계산이다.
_REPO_ROOT: Final = Path(__file__).resolve().parents[3]

#: 🔴 **모든 `BaseSettings` 의 `env_file` 은 이것을 쓴다** — 문자열 `".env"` 를 직접 적지 마라
#: (`tests/ai/contract/test_env_file_anchoring.py` 가 막는다).
#: ⚠ **순서가 계약이다.** 앵커가 먼저, CWD 가 나중 — **뒤가 이긴다**(모듈 docstring).
ENV_FILES: Final[tuple[Path, str]] = (_REPO_ROOT / ".env", ".env")
