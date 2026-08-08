"""실행 컨텍스트와 실행 메타 — 재현성의 단위.

사양 원본: docs/06_erd.md AI_RUN 테이블 (RunMetadata와 필드 1:1)
         docs/04_api_contract.md §2.2 응답 envelope의 meta.versions
소유: [A+B] 양자 승인 — 변경 시 두 명 승인 필수 (docs/02_ownership.md §4)

불변식 8(CLAUDE.md): 모든 실행은 AI_RUN(버전 세트 + snapshot_hash)을 기록한다.
동일 입력 + 동일 버전 = 동일 출력(결정론 경로는 바이트 동일).

버전 세트는 공통 6종(pipeline·engine·threshold·prompt·schema·contract)과
B 실행 전용 nullable 4종(graph·taxonomy·verify_config·difficulty_calib)이다.
과거 두 문서가 각각 4종씩 서로 다르게 적고 있었고(§2.2=threshold·contract 포함,
ERD=prompt·schema 포함), 7/15 판단으로 합집합인 6종에 통일하면서 ERD·계약 문서를
같이 고쳤다.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Capability(StrEnum):
    """AI_RUN.capability — A·B capability의 공용 값 집합."""

    DETECTION = "detection"
    COMPOSITION = "composition"
    IMPORT_MAPPING = "import_mapping"
    DIAGNOSIS = "diagnosis"
    PROBLEM_GENERATION = "problem_generation"


class VersionSet(BaseModel):
    """재현성 키가 되는 버전 묶음 — 공통 6종 + B nullable 4종.

    이 버전 세트 + input_snapshot_hash가 같으면 같은 출력이 나와야 한다.
    API 응답의 meta.versions로도 항상 실린다 (04_api_contract.md §2.2).

    🔴 같은 출력의 조건에는 `generation_params`(seed·temperature)도 든다.
    ⚠ LLM 경로의 seed는 서버 best-effort라 이 조건이 성립해도 **바이트 동일은 보장되지
    않는다**(99 ㊼). 결정론 경로(게이트·판정·산식)는 그래도 **바이트 동일**이다 —
    불변식 8이 죽는 게 아니라 **경로별로 갈린다.**
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pipeline_version: str
    engine_version: str
    schema_version: str
    contract_version: str
    """API 계약 버전 — meta.versions.contract. 전 실행에 실린다."""

    threshold_version: str | None = None
    """감지 임계값 시트의 버전 — **detection 실행에만 의미가 있다.**

    임계값은 코드가 아니라 DB threshold_config에 버전 관리되므로
    (03_coding_rules.md §1), 어느 버전으로 판정했는지를 남기지 않으면 과거 경보를
    재현할 수 없다(불변식 8). 골든셋도 "시트 개정 시 동시 개정"으로 이 버전에
    연동된다(평가 계획서 §2).

    composition·import_mapping은 임계값을 쓰지 않으므로 None이다.
    """

    prompt_version: str | None = None
    """🔴 **선언 축** — **프롬프트를 쓰지 않는 capability에서만 None**이다(04 §2.2).

    counsel·classify·detect·imports·pg는 **전부 프롬프트를 쓰므로 호출 0건인 실행에서도
    채운다**(캐시 히트 포함). ⚠ **「LLM 미사용 실행」이라는 표현을 쓰지 않는다** — 그 말이
    ⓐ*"LLM을 안 쓰는 capability"* / ⓑ*"호출이 0인 실행"* 둘로 읽혀 **실제 오독을 낳았다**
    (99 ㊧·#11 ⓓ · 04가 8/10에 폐기).

    ⚠ **(8/8 정정 · 99 #12) 종전 문면은 *"LLM 미사용 실행(감지 등)에서는 None"* 이었고
    「(감지 등)」이 04의 8/8 정정과 정반대였다** — **감지는 브리핑 프롬프트 `0.2`를 실제로
    쓴다**(`detect.py`의 `detection_versions()`). 표현만이 아니라 **내용이 틀렸다.**
    """

    graph_version: str | None = None
    """curriculum_graph.yaml 버전 — 진단·출제 외 실행에서는 None."""

    taxonomy_version: str | None = None
    """수능 영역·유형 공용 어휘 버전 — 진단·출제 외 실행에서는 None."""

    verify_config_version: str | None = None
    """B 진단·품질 게이트 설정 버전 — 관련 실행 외에는 None."""

    difficulty_calib_version: str | None = None
    """난이도 보정 버전 — 문항 생성 외 실행에서는 None."""


class GenerationParams(BaseModel):
    """AI_RUN.generation_params(jsonb)의 타입 — LLM 샘플링 파라미터.

    seed는 "동일 입력 = 동일 출력" 불변식의 재현 키다 (03_coding_rules.md §3).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    temperature: float | None = Field(default=None, ge=0.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, gt=0)
    seed: int | None = None


class ExecutionContext(BaseModel):
    """실행 전 구간을 흐르는 불변 컨텍스트.

    capability 코드는 이 객체를 주입받아 산출물에 execution_id를 달고,
    실행이 끝나면 RunMetadata로 승격해 AI_RUN에 기록한다.
    전 테이블 tenant_id 필수 — 테넌트 격리 없는 쿼리는 반려 (CLAUDE.md §4).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: UUID
    tenant_id: str = Field(min_length=1)
    """teacher alias · RLS 키 — 실명이 아니다 (불변식 3)."""

    capability: Capability
    input_snapshot_hash: str = Field(min_length=1)
    """재현성 키 — 입력 스냅숏의 canonical json 해시 (03_coding_rules.md §1b)."""

    versions: VersionSet

    def to_run_metadata(
        self,
        created_at: datetime,
        model_provider: str | None = None,
        model_name: str | None = None,
        generation_params: GenerationParams | None = None,
    ) -> "RunMetadata":
        """AI_RUN에 기록할 메타로 승격한다.

        created_at은 주입받는다 — datetime.now() 직접 호출 금지(clock 주입,
        03_coding_rules.md §3). 🔴 **그 실행의 LLM 호출이 0건이면** `model_*`·
        `generation_params`를 비운다 — **사용 축**이다(선언 축인 버전 키와 다르다).
        """
        return RunMetadata(
            execution_id=self.execution_id,
            tenant_id=self.tenant_id,
            capability=self.capability,
            pipeline_version=self.versions.pipeline_version,
            engine_version=self.versions.engine_version,
            threshold_version=self.versions.threshold_version,
            prompt_version=self.versions.prompt_version,
            schema_version=self.versions.schema_version,
            contract_version=self.versions.contract_version,
            graph_version=self.versions.graph_version,
            taxonomy_version=self.versions.taxonomy_version,
            verify_config_version=self.versions.verify_config_version,
            difficulty_calib_version=self.versions.difficulty_calib_version,
            model_provider=model_provider,
            model_name=model_name,
            generation_params=generation_params,
            input_snapshot_hash=self.input_snapshot_hash,
            created_at=created_at,
        )


class RunMetadata(BaseModel):
    """AI_RUN 레코드 1행 — ERD와 필드 1:1.

    필드를 더하거나 빼면 ERD(docs/06_erd.md)를 같이 고쳐야 하고, 이 파일은
    양자 승인 대상이다. 순서·이름 모두 ERD 표기를 따른다.
    """

    # protected_namespaces=() — ERD 필드명 model_provider·model_name을 1:1로
    # 유지하기 위해 pydantic의 `model_` 보호 네임스페이스를 비운다.
    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    execution_id: UUID
    tenant_id: str = Field(min_length=1)
    capability: Capability
    pipeline_version: str
    engine_version: str
    threshold_version: str | None = None
    """감지 임계값 시트 버전 — detection 외에는 null (VersionSet 참조)."""

    prompt_version: str | None = None
    """🔴 **선언 축** — 프롬프트를 쓰지 않는 capability에서만 null(`VersionSet` 참조)."""

    schema_version: str
    contract_version: str
    """API 계약 버전 — meta.versions.contract (04_api_contract.md §2.2)."""

    graph_version: str | None = None
    """curriculum_graph.yaml 버전 — 진단·출제 외에는 null."""

    taxonomy_version: str | None = None
    """수능 영역·유형 공용 어휘 버전 — 진단·출제 외에는 null."""

    verify_config_version: str | None = None
    """B 진단·품질 게이트 설정 버전 — 관련 실행 외에는 null."""

    difficulty_calib_version: str | None = None
    """난이도 보정 버전 — 문항 생성 외에는 null."""

    model_provider: str | None = None
    """🔴 **사용 축** — **그 실행의 LLM 호출이 0건이면 null**이다.

    ⚠ **선언 축(버전 키)과 조건이 다르다** — 버전 키는 capability로 갈리고 이 셋
    (`model_provider`·`model_name`·`generation_params`)은 **실제 호출 유무**로 갈린다.
    같은 행 안에 두 축이 산다(04 §2.2 · 99 ㊧).
    """

    model_name: str | None = None
    generation_params: GenerationParams | None = None
    input_snapshot_hash: str = Field(min_length=1)
    created_at: datetime
