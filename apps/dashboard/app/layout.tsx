import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Marketing Content OS",
  description: "Orquestacion de trabajos de contenido multimedia",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="es">
      <body style={{ margin: 0, fontFamily: "Arial, sans-serif", background: "#f5f7fa" }}>
        {children}
      </body>
    </html>
  );
}
