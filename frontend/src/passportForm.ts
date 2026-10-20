export function validatePassportForm(input: {
  username: string;
  resume: File | null;
  role: string;
  certificates: File[];
}): string | null {
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
  if (input.certificates.some((file) => !isPdf(file))) return "All certificates must be PDF files.";
  if (input.certificates.some((file) => file.size === 0)) return "An uploaded certificate PDF is empty. Choose a file with content or remove it.";
  if (input.certificates.some((file) => file.size > 10 * 1024 * 1024)) {
    return "Each certificate PDF must be 10 MB or smaller.";
  }
  if (input.resume.size + input.certificates.reduce((total, file) => total + file.size, 0) > 25 * 1024 * 1024) {
    return "The resume and certificates must total 25 MB or less.";
  }
  return null;
}

function isPdf(file: File): boolean {
  return file.type === "application/pdf" || (!file.type && file.name.toLowerCase().endsWith(".pdf"));
}
