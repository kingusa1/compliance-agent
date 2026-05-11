/**
 * Strip the supplier-script prefix the upload pipeline glues onto stored
 * filenames so the UI shows the original recording name only.
 *
 * Input  "EON_Next__E.ON_Next_NHH+HH_Verbal_Contract_Script_(TPI)__Ms Bonnie Clarke.mp3"
 * Output "Ms Bonnie Clarke.mp3"
 *
 * If no `__<originalname>` marker is found, returns the input unchanged.
 */
export function shortFilename(filename: string | null | undefined): string {
  if (!filename) return "—";
  const m = filename.match(/__([^_].*\.(mp3|wav|m4a|aac|ogg|flac))$/i);
  return m ? m[1] : filename;
}
