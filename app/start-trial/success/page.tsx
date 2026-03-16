import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function StartTrialSuccessPage(): JSX.Element {
  return (
    <main className="min-h-screen bg-slate-50">
      <section className="mx-auto max-w-2xl px-4 py-14 md:px-8">
        <Card>
          <CardHeader>
            <CardTitle>Trial request submitted</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-slate-700">
            <p>
              We have received your trial request. A consultant will contact you to confirm template choice and trial
              activation details.
            </p>
            <p>The request is already routed into MOY internal conversion tracking workflow.</p>
            <div className="flex flex-wrap gap-2">
              <Button asChild>
                <Link href="/product">View Product Capabilities</Link>
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
