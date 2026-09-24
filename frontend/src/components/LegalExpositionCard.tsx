import type { ApiDecimal, ExposureMatrix } from "@/lib/types";

type Props = {
  matrix: ExposureMatrix;
  summary: string;
};

function euro(value?: ApiDecimal | null) {
  if (value === undefined || value === null || !Number.isFinite(Number(value))) return "Non chiffré";
  return new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(Number(value));
}

function iconFor(category: string) {
  if (category === "ADMINISTRATIVE") return "§";
  if (category === "PENAL") return "!";
  if (category === "CIVIL") return "↗";
  return "·";
}

export default function LegalExpositionCard({ matrix, summary }: Props) {
  return (
    <section className="exposure-panel" aria-label="Exposition juridique et financière">
      <div className="exposure-title"><span aria-hidden="true">◈</span> Exposition juridique estimée</div>
      <div className="exposure-amount">
        {matrix.max_known_fixed_fine_eur !== undefined && matrix.max_known_fixed_fine_eur !== null
          ? `Jusqu’à ${euro(matrix.max_known_fixed_fine_eur)}`
          : "Aucun plafond fixe calculé"}
      </div>
      <p className="exposure-summary">{summary}</p>
      {matrix.items.map((item, index) => (
        <div className="exposure-item" key={`${item.category}-${item.legal_basis ?? item.title}-${index}`}>
          <div className="exposure-item-title">
            <span aria-hidden="true" style={{ marginRight: 6 }}>{iconFor(item.category)}</span>
            {item.title}
          </div>
          {item.legal_basis && <p className="exposure-item-note">{item.legal_basis}</p>}
          <p className="exposure-item-note">{item.note}</p>
        </div>
      ))}
      {matrix.civil_risk.length > 0 && (
        <div className="exposure-item">
          <div className="exposure-item-title">Risque civil — non chiffré</div>
          {matrix.civil_risk.map((risk) => <p key={risk} className="exposure-item-note">{risk}</p>)}
        </div>
      )}
      <div className="exposure-item">
        <p className="exposure-item-note">
          Les plafonds ne sont pas des amendes automatiques. L’outil n’additionne pas les régimes et ne calcule pas les dommages civils.
        </p>
      </div>
    </section>
  );
}
