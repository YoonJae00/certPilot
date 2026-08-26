/**
 * 지식 그래프 API 클라이언트 (PRD §7 F3·F5·F8).
 *
 * 공통 fetch 래퍼(`apiFetch`)는 `lib/api.ts` 것을 그대로 쓴다.
 */

import { apiFetch } from "@/lib/api";
import type { ProjectGraph } from "@/lib/types-graph";

export const graphApi = {
  /**
   * 프로젝트 지식 그래프 한 벌(노드·엣지)을 받는다.
   *
   * 완료된 모의심사가 없어도 200 으로 골격(장·절·항목·문서·증적)을 준다.
   * 그때는 `assessment_id` 가 null 이고 판정·인용 엣지가 비어 있다.
   */
  get(projectId: string, signal?: AbortSignal): Promise<ProjectGraph> {
    return apiFetch<ProjectGraph>(`/projects/${projectId}/graph`, { signal });
  },
};
