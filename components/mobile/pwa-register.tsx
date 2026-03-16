"use client";

import { useEffect } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { mobileClientService } from "@/services/mobile-client-service";

function detectInstallType(): "browser" | "pwa" {
  if (typeof window === "undefined") return "browser";
  const standalone = window.matchMedia("(display-mode: standalone)").matches;
  return standalone ? "pwa" : "browser";
}

export function PwaRegister(): null {
  const { user } = useAuth();

  useEffect(() => {
    if (typeof window === "undefined") return;
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => null);
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const updateState = () => {
      window.document.documentElement.dataset.network = navigator.onLine ? "online" : "offline";
    };
    updateState();
    window.addEventListener("online", updateState);
    window.addEventListener("offline", updateState);
    return () => {
      window.removeEventListener("online", updateState);
      window.removeEventListener("offline", updateState);
    };
  }, []);

  useEffect(() => {
    if (!user) return;
    const deviceLabel = `${navigator.platform || "mobile"}-${navigator.userAgent.slice(0, 32)}`;
    const installType = detectInstallType();
    void mobileClientService
      .registerInstallSession({
        deviceLabel,
        installType,
        appVersion: process.env.NEXT_PUBLIC_APP_VERSION ?? "phase-13",
        pushCapable: "Notification" in window,
        metadata: {
          userAgent: navigator.userAgent,
          language: navigator.language
        }
      })
      .catch(() => null);
  }, [user]);

  return null;
}
