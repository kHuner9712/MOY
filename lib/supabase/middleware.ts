import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import { getSupabasePublicEnv, hasSupabasePublicEnv } from "@/lib/supabase/env";

const AUTH_PATH = "/login";
const MARKETING_PUBLIC_PREFIXES = ["/product", "/industries", "/request-demo", "/start-trial", "/contact"];

function isPublicPath(pathname: string): boolean {
  if (pathname.startsWith("/_next")) return true;
  if (pathname.startsWith("/api")) return true;
  if (pathname === "/") return true;
  if (MARKETING_PUBLIC_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`))) return true;
  if (pathname === "/favicon.ico") return true;
  if (pathname === "/manifest.webmanifest") return true;
  if (pathname === "/sw.js") return true;
  if (pathname.startsWith("/icons")) return true;
  if (pathname.startsWith("/assets")) return true;
  return false;
}

export async function updateSession(request: NextRequest): Promise<NextResponse> {
  if (!hasSupabasePublicEnv()) {
    return NextResponse.next({
      request
    });
  }

  const { url, anonKey } = getSupabasePublicEnv();
  let response = NextResponse.next({
    request
  });

  const supabase = createServerClient(url, anonKey, {
    cookies: {
      get(name: string): string | undefined {
        return request.cookies.get(name)?.value;
      },
      set(name: string, value: string, options: CookieOptions): void {
        request.cookies.set({
          name,
          value,
          ...options
        });
        response = NextResponse.next({
          request
        });
        response.cookies.set({
          name,
          value,
          ...options
        });
      },
      remove(name: string, options: CookieOptions): void {
        request.cookies.set({
          name,
          value: "",
          ...options
        });
        response = NextResponse.next({
          request
        });
        response.cookies.set({
          name,
          value: "",
          ...options,
          maxAge: 0
        });
      }
    }
  });

  const {
    data: { user }
  } = await supabase.auth.getUser();

  const pathname = request.nextUrl.pathname;
  if (isPublicPath(pathname)) return response;

  const isLoginPath = pathname.startsWith(AUTH_PATH);

  if (!user && !isLoginPath) {
    const url = request.nextUrl.clone();
    url.pathname = AUTH_PATH;
    url.searchParams.set("redirectTo", pathname);
    return NextResponse.redirect(url);
  }

  if (user && isLoginPath) {
    const url = request.nextUrl.clone();
    url.pathname = "/dashboard";
    url.searchParams.delete("redirectTo");
    return NextResponse.redirect(url);
  }

  return response;
}
