"use client";

import { AuthProvider } from "@/components/auth/auth-provider";
import { PwaRegister } from "@/components/mobile/pwa-register";
import { AppDataProvider } from "@/components/shared/app-data-provider";

export function RootProviders({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <AuthProvider>
      <AppDataProvider>
        <PwaRegister />
        {children}
      </AppDataProvider>
    </AuthProvider>
  );
}
