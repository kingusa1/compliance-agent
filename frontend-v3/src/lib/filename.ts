/**
 * Strip the supplier-script prefix the upload pipeline glues onto stored
 * filenames so the UI shows the original recording name only.
 *
 * Input  "EON_Next__E.ON_Next_NHH+HH_Verbal_Contract_Script_(TPI)__Ms Bonnie Clarke.mp3"
 * Output "Ms Bonnie Clarke.mp3"
 *
 * The upload pipeline can stack multiple "__" separators, so use the LAST
 * occurrence (not the first) — a greedy regex was returning the
 * "E.ON_Next_NHH+HH_..._Ms Bonnie Clarke.mp3" middle slice. If no
 * `__<originalname>` marker is found, returns the input unchanged.
 */
export function shortFilename(filename: string | null | undefined): string {
  if (!filename) return "—";
  const idx = filename.lastIndexOf("__");
  if (idx === -1) return filename;
  const tail = filename.slice(idx + 2);
  return tail || filename;
}
