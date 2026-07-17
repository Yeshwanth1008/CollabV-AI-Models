import Link from "next/link";
import { ArrowRight, Brain, FileText, Zap } from "lucide-react";

export default function Landing() {
  return (
    <div className="space-y-24 pb-24">
      <section className="text-center space-y-6 pt-12">
        <div className="inline-block rounded-full border border-border bg-card/40 px-4 py-1 text-sm text-muted-foreground">
          v3.0 — Patent scoring, readiness prediction, deal probability
        </div>
        <h1 className="text-5xl md:text-6xl font-bold tracking-tight">
          Match Your Innovation with{" "}
          <span className="gradient-text">India&apos;s Top Academic Minds</span>
        </h1>
        <p className="mx-auto max-w-2xl text-lg text-muted-foreground">
          CollabV AI surfaces the right IIT Madras professors for your R&amp;D
          challenges in seconds. Backed by patent valuation, collaboration
          readiness, and deal-success scoring.
        </p>
        <div className="flex justify-center gap-3">
          <Link href="/match" className="btn-primary">
            Find Your Expert <ArrowRight className="ml-2 h-4 w-4" />
          </Link>
          <Link href="/professors" className="btn-ghost">
            Browse Directory
          </Link>
        </div>
      </section>

      <section className="grid md:grid-cols-3 gap-4">
        {[
          ["543", "Professors", "across 16 departments"],
          ["50+", "Research domains", "deeply indexed"],
          ["6", "Scoring models", "for nuanced ranking"],
        ].map(([num, label, sub]) => (
          <div key={label} className="card text-center">
            <div className="text-4xl font-bold gradient-text">{num}</div>
            <div className="mt-2 text-foreground">{label}</div>
            <div className="text-sm text-muted-foreground">{sub}</div>
          </div>
        ))}
      </section>

      <section className="space-y-8">
        <h2 className="text-3xl font-semibold text-center">How it works</h2>
        <div className="grid md:grid-cols-3 gap-6">
          {[
            { icon: FileText, t: "Describe your need", d: "Paste a plain-text brief. The AI parses it into structured requirements." },
            { icon: Brain, t: "Smart ranking", d: "6 scoring layers consider research alignment, patents, readiness, and deal probability." },
            { icon: Zap, t: "Decide & act", d: "Get talking points, risk factors, and templated MoUs to start the engagement." },
          ].map(({ icon: Icon, t, d }) => (
            <div key={t} className="card">
              <Icon className="h-8 w-8 text-primary mb-3" />
              <h3 className="text-lg font-medium">{t}</h3>
              <p className="text-sm text-muted-foreground mt-1">{d}</p>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
