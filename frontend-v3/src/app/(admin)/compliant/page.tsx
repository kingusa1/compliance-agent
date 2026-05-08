"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function CompliantRedirectPage() {
  const router = useRouter();
  useEffect(() => { router.replace("/tracker?tab=compliant"); }, [router]);
  return null;
}
