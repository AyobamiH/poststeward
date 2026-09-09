import { readFileSync } from "node:fs";

const file = process.argv[2] || "audit.json";
let report;
try {
  report = JSON.parse(readFileSync(file, "utf8"));
} catch {
  throw new Error("npm audit did not produce parseable JSON.");
}
const vulnerabilities = Object.entries(report.vulnerabilities || {})
  .map(([name, value]) => ({
    name,
    severity: value.severity,
    direct: value.isDirect === true,
    range: value.range,
    effects: Array.isArray(value.effects) ? [...value.effects].sort() : [],
    via: Array.isArray(value.via)
      ? value.via.map((entry) =>
          typeof entry === "string"
            ? entry
            : {
                source: entry.source,
                name: entry.name,
                severity: entry.severity,
                range: entry.range,
                title: entry.title,
              },
        )
      : [],
    fixAvailable:
      value.fixAvailable && typeof value.fixAvailable === "object"
        ? {
            name: value.fixAvailable.name,
            version: value.fixAvailable.version,
            semverMajor: value.fixAvailable.isSemVerMajor === true,
          }
        : value.fixAvailable === true,
  }))
  .sort((a, b) => a.name.localeCompare(b.name));
const summary = {
  auditReportVersion: report.auditReportVersion,
  metadata: report.metadata?.vulnerabilities || {},
  vulnerabilities,
};
console.log("POSTSTEWARD_FULL_AUDIT " + JSON.stringify(summary));
