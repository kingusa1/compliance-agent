"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function RejectionsRedirectPage() {
  const router = useRouter();
  useEffect(() => { router.replace("/tracker?tab=active"); }, [router]);
  return null;
}
