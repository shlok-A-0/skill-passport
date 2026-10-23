export function validatePassportForm(input: {
  username: string;
  resume: File | null;
  role: string;
  certificates: File[];
  reports?: File[];
  portfolioUrl?: string;
}): string | null {
  const reports = input.reports ?? [];
  const username = input.username.trim();
  if (!username) return "Enter your GitHub username.";
  if (!/^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/.test(username)) {
    return "Enter a GitHub username using 1–39 letters and numbers, with single hyphens between characters.";
  }
  if (!input.resume) return "Upload your resume PDF before generating a passport.";
  if (!isPdf(input.resume)) return "Your resume must be a PDF file.";
  if (input.resume.size === 0) return "Your resume PDF is empty. Choose a file with content.";
  if (input.resume.size > 10 * 1024 * 1024) return "Your resume PDF must be 10 MB or smaller.";
  if (!input.role) return "Choose a target role.";
  if (input.certificates.length > 5) return "Upload no more than 5 certificates.";
  if (input.certificates.some((file) => !isSupportedCertificate(file))) return "Certificates must be PDFs, PNG images, or JPEG images.";
  if (input.certificates.some((file) => file.size === 0)) return "An uploaded certificate is empty. Choose a file with content or remove it.";
  if (input.certificates.some((file) => file.size > 10 * 1024 * 1024)) {
    return "Each certificate must be 10 MB or smaller.";
  }
  if (reports.length > 3) return "Upload no more than 3 project reports.";
  if (reports.some((file) => !isSupportedCertificate(file))) return "Project reports must be PDFs, PNG images, or JPEG images.";
  if (reports.some((file) => file.size === 0)) return "An uploaded project report is empty. Choose a file with content or remove it.";
  if (reports.some((file) => file.size > 10 * 1024 * 1024)) return "Each project report must be 10 MB or smaller.";
  if (input.resume.size + [...input.certificates, ...reports].reduce((total, file) => total + file.size, 0) > 25 * 1024 * 1024) {
    return reports.length
      ? "The resume, certificates and reports must total 25 MB or less."
      : "The resume and certificates must total 25 MB or less.";
  }
  return portfolioUrlError(input.portfolioUrl ?? "");
}

/** Same rule as the backend: empty, or one https:// link of at most 300 characters. */
export function portfolioUrlError(value: string): string | null {
  const url = value.trim();
  if (!url) return null;
  if (url.length > 300 || /\s/.test(url)) return "The portfolio URL must be a single https:// link of at most 300 characters.";
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || !parsed.hostname || parsed.username || parsed.password) {
      return "The portfolio URL must start with https://.";
    }
  } catch {
    return "The portfolio URL must start with https://.";
  }
  return null;
}

function isPdf(file: File): boolean {
  return file.type === "application/pdf" || (!file.type && file.name.toLowerCase().endsWith(".pdf"));
}

function isSupportedCertificate(file: File): boolean {
  const type = file.type.toLowerCase();
  const name = file.name.toLowerCase();
  return isPdf(file)
    || type === "image/png" || type === "image/jpeg"
    || (!type && /\.(png|jpe?g)$/.test(name));
}
