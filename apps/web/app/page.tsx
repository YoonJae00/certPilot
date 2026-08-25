import { redirect } from "next/navigation";

/** 루트는 프로젝트 목록으로 보낸다. 미인증이면 목록 화면의 가드가 /login 으로 넘긴다. */
export default function RootPage() {
  redirect("/projects");
}
