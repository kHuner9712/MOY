"use client";

import { useEffect, useState } from "react";
import { Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
}

export function InstallPromptCard(): JSX.Element | null {
  const [deferredPrompt, setDeferredPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [installed, setInstalled] = useState(false);

  useEffect(() => {
    const onBeforeInstallPrompt = (event: Event) => {
      event.preventDefault();
      setDeferredPrompt(event as BeforeInstallPromptEvent);
    };
    const onInstalled = () => {
      setInstalled(true);
      setDeferredPrompt(null);
    };

    window.addEventListener("beforeinstallprompt", onBeforeInstallPrompt);
    window.addEventListener("appinstalled", onInstalled);

    return () => {
      window.removeEventListener("beforeinstallprompt", onBeforeInstallPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (installed) {
    return (
      <Card className="mb-3">
        <CardContent className="p-3 text-xs text-emerald-700">已安装为轻应用，可从主屏幕快速打开 MOY。</CardContent>
      </Card>
    );
  }

  if (!deferredPrompt) return null;

  return (
    <Card className="mb-3 border-sky-200 bg-sky-50/60">
      <CardContent className="flex items-center justify-between gap-2 p-3">
        <div>
          <p className="text-sm font-semibold text-sky-900">安装 MOY 轻应用</p>
          <p className="text-xs text-sky-800">添加到主屏幕后可更快打开 today / capture。</p>
        </div>
        <Button
          size="sm"
          onClick={async () => {
            await deferredPrompt.prompt();
            await deferredPrompt.userChoice;
            setDeferredPrompt(null);
          }}
        >
          <Download className="mr-1 h-3 w-3" />
          安装
        </Button>
      </CardContent>
    </Card>
  );
}
