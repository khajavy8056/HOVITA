import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HOVITA — پنل شخصیت هوشمند مستقل",
  description: "سامانه جامع ساخت، تثبیت هویت، تولید، تایید و یادگیری خودکار شخصیت دیجیتال",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="fa" dir="rtl">
      <body>{children}</body>
    </html>
  );
}
