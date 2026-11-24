/**
 * The investor brief PDF: one company, the few things an investor or analyst reads
 * first — key figures, the price and filed-results charts, the report's own synthesis,
 * catalysts, risks, open questions, confidence and sources. Dark theme of the product.
 */

import type { ReactNode } from "react";
import { Document, Link, Page, Text, View } from "@react-pdf/renderer";
import { C, s } from "./theme";
import { FinancialBars, PriceChart } from "./charts";
import { day } from "./format";
import type { BriefItem, InvestorBrief, Kpi } from "./investorBrief";

const CONTENT_W = 595.28 - 72;

function KpiTile({ k, width }: { k: Kpi; width: number }) {
  const color = k.tone === "positive" ? C.positive : k.tone === "negative" ? C.negative : C.ink;
  return (
    <View style={[s.card, { width, padding: 8, marginBottom: 6 }]} wrap={false}>
      <Text style={s.eyebrow}>{k.label}</Text>
      <Text style={{ fontSize: width < 110 ? 11.5 : 13, fontWeight: 600, color, marginTop: 3, lineHeight: 1.2 }}>{k.value}</Text>
      {k.sub ? <Text style={[s.small, { marginTop: 2 }]}>{k.sub}</Text> : null}
    </View>
  );
}

function Items({ title, items, dot, empty }: { title: string; items: BriefItem[]; dot: string; empty: string }) {
  return (
    <View style={{ width: CONTENT_W }}>
      <Text style={s.h2}>{title}</Text>
      {items.length === 0 ? (
        <Text style={s.small}>{empty}</Text>
      ) : (
        items.map((it, i) => (
          <View key={i} style={{ flexDirection: "row", marginBottom: 6 }} wrap={false}>
            <View style={{ width: 4, height: 4, borderRadius: 2, backgroundColor: dot, marginTop: 4, marginRight: 6 }} />
            {/* A fixed width, not flex: react-pdf measures a flexed text column before
                the page break and overlaps its lines on the next page. */}
            <View style={{ width: CONTENT_W - 10 }}>
              <Text style={s.body}>{it.text}</Text>
              {it.meta ? <Text style={[s.small, { marginTop: 1 }]}>{it.meta}</Text> : null}
            </View>
          </View>
        ))
      )}
    </View>
  );
}

function ChartCard({ title, caption, children }: { title: string; caption: string; children: ReactNode }) {
  return (
    <View style={[s.card, { marginTop: 4 }]} wrap={false}>
      <View style={{ flexDirection: "row", justifyContent: "space-between", marginBottom: 6 }}>
        <Text style={[s.eyebrow, { color: C.ink2 }]}>{title}</Text>
      </View>
      {children}
      <Text style={[s.small, { marginTop: 4 }]}>{caption}</Text>
    </View>
  );
}

export default function InvestorBriefPdf({ brief, generatedAt }: { brief: InvestorBrief; generatedAt: string }) {
  // Five tiles sit on one row; six as two rows of three; otherwise rows of four.
  const perRow = brief.kpis.length === 5 ? 5 : brief.kpis.length === 6 ? 3 : 4;
  const gap = 6;
  const tileW = (CONTENT_W - gap * (perRow - 1)) / perRow;
  const half = (CONTENT_W - 10) / 2;
  return (
    <Document title={`${brief.name} — investor brief`} author="InvestingBuddy" subject="Investor brief" creator="InvestingBuddy">
      <Page size="A4" style={s.page}>
        {/* Header */}
        <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" }}>
          <View style={{ flex: 1 }}>
            <Text style={s.eyebrow}>Investor brief · InvestingBuddy research</Text>
            <Text style={s.title}>{brief.name}</Text>
            <Text style={s.subtitle}>
              {[brief.ticker, brief.exchange].filter(Boolean).join(" · ")}
              {brief.reportDate ? `   ·   Report of ${day(brief.reportDate)}` : ""}
            </Text>
          </View>
          <View style={{ alignItems: "flex-end" }}>
            <View style={{ backgroundColor: C.accentQuiet, borderRadius: 10, paddingVertical: 3, paddingHorizontal: 8 }}>
              <Text style={{ fontSize: 7, color: C.accent, fontWeight: 600 }}>
                {(brief.reviewStatus ?? "draft").toUpperCase()} · HUMAN REVIEW REQUIRED
              </Text>
            </View>
          </View>
        </View>

        <View style={{ height: 0.75, backgroundColor: C.line, marginTop: 12, marginBottom: 10 }} />

        {/* KPIs */}
        {brief.kpis.length > 0 && (
          <View style={{ flexDirection: "row", flexWrap: "wrap", justifyContent: "flex-start" }}>
            {brief.kpis.map((k, i) => (
              <View key={k.label} style={{ marginRight: (i + 1) % perRow === 0 ? 0 : gap }}>
                <KpiTile k={k} width={tileW} />
              </View>
            ))}
          </View>
        )}

        {/* Price */}
        <ChartCard title="Share price" caption={brief.price.caption}>
          <PriceChart
            points={brief.price.points}
            width={CONTENT_W - 20}
            height={150}
            emptyText={brief.price.unavailable ?? "No price series available"}
          />
        </ChartCard>

        {/* Financials + summary */}
        <View style={{ flexDirection: "row", marginTop: 6 }}>
          <View style={{ width: half, marginRight: 10 }}>
            <ChartCard title="Filed annual results" caption={brief.financials.caption}>
              {brief.financials.unavailable ? (
                <FinancialBars rows={[]} width={half - 20} height={130} />
              ) : (
                <FinancialBars rows={brief.financials.rows} width={half - 20} height={130} />
              )}
              <View style={{ flexDirection: "row", marginTop: 4 }}>
                <View style={{ width: 6, height: 6, backgroundColor: C.accent, borderRadius: 1, marginRight: 3, marginTop: 1 }} />
                <Text style={[s.small, { marginRight: 10 }]}>Revenue</Text>
                <View style={{ width: 6, height: 6, backgroundColor: C.positive, borderRadius: 1, marginRight: 3, marginTop: 1 }} />
                <Text style={s.small}>Net income (red when a loss)</Text>
              </View>
              {brief.financials.unavailable ? <Text style={[s.small, { marginTop: 2 }]}>{brief.financials.unavailable}</Text> : null}
            </ChartCard>
          </View>
          <View style={[s.card, { width: half, marginTop: 4 }]}>
            <Text style={[s.eyebrow, { color: C.ink2 }]}>Investment summary</Text>
            {brief.thesis ? (
              <Text style={[s.body, { marginTop: 6, color: C.ink }]}>
                <Text style={{ color: C.ink3 }}>{"Thesis tested: "}</Text>
                {brief.thesis}
              </Text>
            ) : null}
            {brief.summary.length === 0 ? (
              <Text style={[s.small, { marginTop: 6 }]}>The report holds no synthesis sentences.</Text>
            ) : (
              brief.summary.map((t, i) => (
                <Text key={i} style={[s.body, { marginTop: 5 }]}>
                  {t}
                </Text>
              ))
            )}
            {brief.sizeFit ? <Text style={[s.small, { marginTop: 6 }]}>{brief.sizeFit}</Text> : null}
          </View>
        </View>

        {/* Strengths */}
        {brief.strengths.length > 0 && (
          <Items title="What the evidence supports" items={brief.strengths} dot={C.positive} empty="" />
        )}

        {/* Catalysts, risks, open questions, confidence: one column, so a block that
            runs onto the next page never overlaps its neighbour. */}
        <Items title="Catalysts" items={brief.catalysts} dot={C.accent} empty="No catalyst finding was established." />
        <Items title="Risks and case against" items={brief.risks} dot={C.negative} empty="No adverse finding was recorded." />

        <View wrap={false}>
          <Text style={s.h2}>Open questions</Text>
          {brief.openQuestions.length === 0 ? (
            <Text style={s.small}>None recorded.</Text>
          ) : (
            brief.openQuestions.map((q, i) => (
              <Text key={i} style={[s.body, { marginBottom: 3 }]}>
                {`${i + 1}.  ${q}`}
              </Text>
            ))
          )}
        </View>

        <View wrap={false}>
          <Text style={s.h2}>Verdict and confidence</Text>
          <Text style={s.body}>{brief.verdict ?? "No verdict was computed for this report."}</Text>
          {brief.confidence ? <Text style={[s.small, { marginTop: 3 }]}>{brief.confidence}</Text> : null}
        </View>

        {/* Sources */}
        <View wrap={false}>
          <Text style={s.h2}>Sources for the charts</Text>
          {brief.sources.length === 0 ? (
            <Text style={s.small}>No external market data was read.</Text>
          ) : (
            brief.sources.map((u) => (
              <Link key={u} src={u} style={{ fontSize: 6.5, color: C.accent, textDecoration: "none", marginBottom: 1 }}>
                {u}
              </Link>
            ))
          )}
          <Text style={[s.small, { marginTop: 4 }]}>
            {`Statements cite findings by label (F1, F2…); the full report ${brief.reportId} holds every citation. Market data retrieved ${day(generatedAt)}.`}
          </Text>
        </View>

        <View style={[s.card, { marginTop: 10, backgroundColor: C.surfaceRaised }]} wrap={false}>
          <Text style={[s.small, { color: C.ink2 }]}>{brief.disclaimer}</Text>
        </View>

        <View style={s.footer} fixed>
          <Text>{`InvestingBuddy · ${brief.name} · generated ${day(generatedAt)}`}</Text>
          <Text render={({ pageNumber, totalPages }) => `${pageNumber} / ${totalPages}`} />
        </View>
      </Page>
    </Document>
  );
}
