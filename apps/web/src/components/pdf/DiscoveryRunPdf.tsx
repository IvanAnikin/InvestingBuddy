/**
 * The discovery-run PDF: the brief as entered, what the run checked, the size of every
 * company it looked at against the mandate, and the candidates with their verified
 * attributes. Dark theme of the product.
 */

import type { ReactNode } from "react";
import { Document, Page, Text, View } from "@react-pdf/renderer";
import { C, s } from "./theme";
import { MarketCapBars } from "./charts";
import { DASH, day, money } from "./format";
import type { DiscoveryBrief, DiscoveryRow } from "./discoveryBrief";

const CONTENT_W = 595.28 - 72;
const TONE = { eligible: C.positive, unverified: C.warning, excluded: C.ink3 } as const;

const COLS: { key: string; title: string; width: number }[] = [
  { key: "company", title: "Company", width: 146 },
  { key: "country", title: "Listing", width: 62 },
  { key: "cap", title: "Market cap", width: 74 },
  { key: "growth", title: "Growth", width: 90 },
  { key: "status", title: "Status", width: CONTENT_W - 146 - 62 - 74 - 90 },
];

function Cell({ width, children, color }: { width: number; children: ReactNode; color?: string }) {
  return (
    <View style={{ width, paddingRight: 6 }}>
      <Text style={{ fontSize: 7.5, color: color ?? C.ink2 }}>{children}</Text>
    </View>
  );
}

function CandidateRow({ r, last }: { r: DiscoveryRow; last: boolean }) {
  return (
    <View
      wrap={false}
      style={{
        flexDirection: "row",
        paddingVertical: 6,
        borderBottomColor: C.line,
        borderBottomWidth: last ? 0 : 0.6,
      }}
    >
      <View style={{ width: COLS[0].width, paddingRight: 6 }}>
        <Text style={{ fontSize: 8, color: C.ink, fontWeight: 600 }}>{r.name}</Text>
        <Text style={s.small}>{[r.ticker, r.exchange].filter(Boolean).join(" · ") || DASH}</Text>
        {r.industry ? <Text style={[s.small, { marginTop: 2 }]}>{r.industry}</Text> : null}
      </View>
      <Cell width={COLS[1].width}>{r.country ?? DASH}</Cell>
      <View style={{ width: COLS[2].width, paddingRight: 6 }}>
        <Text style={{ fontSize: 7.5, color: C.ink2 }}>{money(r.marketCapUsd, "USD")}</Text>
        {r.marketCapAsOf ? <Text style={s.small}>{day(r.marketCapAsOf)}</Text> : null}
      </View>
      <Cell width={COLS[3].width}>{r.growth ?? DASH}</Cell>
      <View style={{ width: COLS[4].width }}>
        <View style={{ flexDirection: "row", alignItems: "center" }}>
          <View style={{ width: 5, height: 5, borderRadius: 2.5, backgroundColor: TONE[r.status], marginRight: 4 }} />
          <Text style={{ fontSize: 7.5, color: C.ink }}>{r.statusWord}</Text>
        </View>
        {r.reason ? <Text style={[s.small, { marginTop: 2 }]}>{r.reason}</Text> : null}
        {r.reportId ? <Text style={[s.small, { marginTop: 2, color: C.accent }]}>Report on file</Text> : null}
      </View>
    </View>
  );
}

export default function DiscoveryRunPdf({ brief, generatedAt }: { brief: DiscoveryBrief; generatedAt: string }) {
  const tileW = (CONTENT_W - 18) / 4;
  return (
    <Document title={`Discovery — ${brief.thesis.slice(0, 80)}`} author="InvestingBuddy" subject="Discovery run" creator="InvestingBuddy">
      <Page size="A4" style={s.page}>
        <Text style={s.eyebrow}>Discovery run · InvestingBuddy research</Text>
        <Text style={[s.title, { fontSize: 15, lineHeight: 1.3 }]}>{`“${brief.thesis}”`}</Text>
        <Text style={s.subtitle}>
          {`Run of ${day(brief.createdAt)} · ${brief.status}${brief.completedAt ? ` · completed ${day(brief.completedAt)}` : ""}`}
        </Text>

        {brief.chips.length > 0 && (
          <View style={{ flexDirection: "row", flexWrap: "wrap", marginTop: 10 }}>
            {brief.chips.map((c) => (
              <View
                key={c.label}
                style={{ flexDirection: "row", backgroundColor: C.surfaceRaised, borderRadius: 10, paddingVertical: 3, paddingHorizontal: 8, marginRight: 6, marginBottom: 4 }}
              >
                <Text style={{ fontSize: 7, color: C.ink3 }}>{`${c.label}  `}</Text>
                <Text style={{ fontSize: 7, color: C.ink, fontWeight: 500 }}>{c.value}</Text>
              </View>
            ))}
          </View>
        )}

        <View style={{ height: 0.75, backgroundColor: C.line, marginTop: 8, marginBottom: 10 }} />

        <View style={{ flexDirection: "row" }}>
          {brief.kpis.map((k, i) => (
            <View key={k.label} style={[s.card, { width: tileW, marginRight: i === brief.kpis.length - 1 ? 0 : 6, padding: 8 }]}>
              <Text style={s.eyebrow}>{k.label}</Text>
              <Text style={{ fontSize: 15, fontWeight: 600, color: C.ink, marginTop: 3, lineHeight: 1.2 }}>{k.value}</Text>
              {k.sub ? <Text style={[s.small, { marginTop: 1 }]}>{k.sub}</Text> : null}
            </View>
          ))}
        </View>

        <View style={[s.card, { marginTop: 10 }]} wrap={false}>
          <Text style={[s.eyebrow, { color: C.ink2, marginBottom: 6 }]}>Market capitalisation vs. the size mandate</Text>
          <MarketCapBars rows={brief.capBars} mandate={brief.mandateUsd} width={CONTENT_W - 20} />
          <View style={{ flexDirection: "row", marginTop: 6 }}>
            {(["eligible", "unverified", "excluded"] as const).map((t) => (
              <View key={t} style={{ flexDirection: "row", alignItems: "center", marginRight: 12 }}>
                <View style={{ width: 6, height: 6, borderRadius: 1, backgroundColor: TONE[t], marginRight: 3 }} />
                <Text style={s.small}>{t === "eligible" ? "Candidate" : t === "unverified" ? "Size not verified" : "Excluded"}</Text>
              </View>
            ))}
          </View>
        </View>

        {brief.autoReport ? (
          <View style={[s.card, { marginTop: 8, backgroundColor: C.accentQuiet, borderColor: C.accentQuiet }]} wrap={false}>
            <Text style={{ fontSize: 8, color: C.ink }}>{brief.autoReport}</Text>
          </View>
        ) : null}

        <Text style={s.h2}>Companies checked</Text>
        <View style={[s.card, { paddingVertical: 4 }]}>
          <View style={{ flexDirection: "row", paddingVertical: 4, borderBottomColor: C.lineStrong, borderBottomWidth: 0.75 }} fixed>
            {COLS.map((c) => (
              <View key={c.key} style={{ width: c.width }}>
                <Text style={s.eyebrow}>{c.title}</Text>
              </View>
            ))}
          </View>
          {brief.rows.length === 0 ? (
            <Text style={[s.small, { paddingVertical: 8 }]}>The run returned no company.</Text>
          ) : (
            brief.rows.map((r, i) => <CandidateRow key={`${r.ticker}${i}`} r={r} last={i === brief.rows.length - 1} />)
          )}
        </View>

        {brief.leadSources.length > 0 && (
          <View wrap={false}>
            <Text style={s.h2}>Where the leads came from</Text>
            <Text style={s.body}>{brief.leadSources.join("   ·   ")}</Text>
          </View>
        )}

        <View style={[s.card, { marginTop: 12, backgroundColor: C.surfaceRaised }]} wrap={false}>
          <Text style={[s.small, { color: C.ink2 }]}>
            {`${brief.disclaimer} Market capitalisation and growth are as verified by the run, each with its own date; a dash means the run could not verify the figure, not that it is zero.`}
          </Text>
        </View>

        <View style={s.footer} fixed>
          <Text>{`InvestingBuddy · discovery run ${brief.runId.slice(0, 8)} · generated ${day(generatedAt)}`}</Text>
          <Text render={({ pageNumber, totalPages }) => `${pageNumber} / ${totalPages}`} />
        </View>
      </Page>
    </Document>
  );
}
