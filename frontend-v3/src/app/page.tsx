import { redirect } from "next/navigation";

/**
 * Root entry. Reviewers and admins should land on their role-specific
 * page; we punt that decision to /login (which knows the session) and
 * forward unauthed visitors to sign-in.
 */
export default function HomePage() {
  redirect("/login");
}
