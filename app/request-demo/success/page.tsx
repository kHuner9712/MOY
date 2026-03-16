import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function RequestDemoSuccessPage(): JSX.Element {
  return (
    <main className="min-h-screen bg-slate-50">
      <section className="mx-auto max-w-2xl px-4 py-14 md:px-8">
        <Card>
          <CardHeader>
            <CardTitle>Demo request submitted</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-slate-700">
            <p>
              Thank you. Our team will review your request and contact you for scheduling. Your lead has already been
              routed into MOY internal followup flow.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button asChild>
                <Link href="/start-trial">Start Trial Instead</Link>
              </Button>
              <Button asChild variant="outline">
                <Link href="/">Back to Home</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>
    </main>
  );
}
