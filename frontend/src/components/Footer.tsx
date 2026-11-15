import { ACKNOWLEDGEMENT, ACK_SOURCE_URL, DATA_DOIS } from "../lib/acknowledgement";

export function Footer() {
  return (
    <footer className="mt-8 border-t border-rule">
      <div className="mx-auto max-w-6xl space-y-2 px-4 py-6 text-xs leading-relaxed text-muted">
        <p>
          Tombaugh, NASA Space Apps Challenge 2026. Independent, not endorsed by NASA. Images: NASA SPHEREx via IRSA.
          Stars: ESA Gaia DR3. Asteroids: IMCCE SkyBoT. Names: SIMBAD (Harvard CfA mirror).
        </p>
        <p>{ACKNOWLEDGEMENT}</p>
        <p>
          AI disclosure: AI tools (Anthropic's Claude) were used to help write the code and the text of this project.
          The team reviewed and tested the results.
        </p>
        <p>
          Data DOIs:{" "}
          {DATA_DOIS.map((d, i) => (
            <span key={d.doi}>
              {i > 0 && ", "}
              {d.release} <a className="underline" href={`https://doi.org/${d.doi}`}>{d.doi}</a>
            </span>
          ))}
          {" · "}
          <a className="underline" href={ACK_SOURCE_URL}>IRSA acknowledgement</a>
        </p>
      </div>
    </footer>
  );
}
