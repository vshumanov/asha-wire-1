import wire.*;

/**
 * Desktop validation of the pure layer (Digest parsing + Wrap). NOT shipped.
 * Fixed-width measure (1 unit/char) so maxWidth == chars-per-line.
 */
public class DigestTest {
    static int fails = 0;
    static final TextMeasure M = new TextMeasure() {
        public int charWidth(char c) { return 1; }
    };

    public static void main(String[] a) throws Exception {
        // --- reject junk ---
        check("null bytes -> null", Digest.parse((byte[]) null) == null);
        check("no magic -> null", Digest.parse("hello\nworld") == null);
        check("blank -> null", Digest.parse("") == null);

        // --- a normal digest ---
        String src = "WIRE1\n"
                + "Thu 18 Sep 2026\n"
                + "# [RETRO] DOOM runs on a pregnancy test\n"
                + "A modder drove the e-ink display directly.\n"
                + "It renders a few frames per second.\n"
                + "# [TECH] RISC-V laptop board ships\n"
                + "Fully open firmware, socketed RAM.\n"
                + "# New lattice-based scheme proposed\n"      // no tag
                + "Post-quantum, small keys.\n";
        Digest d = Digest.parse(src);
        check("parsed non-null", d != null);
        check("date", d.date().equals("Thu 18 Sep 2026"));
        check("count = 3", d.count() == 3);
        check("tag RETRO", d.item(0).tag.equals("RETRO"));
        check("headline stripped", d.item(0).headline.equals("DOOM runs on a pregnancy test"));
        check("body joined with newline",
                d.item(0).body.equals("A modder drove the e-ink display directly.\n"
                        + "It renders a few frames per second."));
        check("second tag TECH", d.item(1).tag.equals("TECH"));
        check("third has empty tag", d.item(2).tag.equals(""));
        check("third headline", d.item(2).headline.equals("New lattice-based scheme proposed"));

        // --- the 10-item cap is enforced on-device ---
        StringBuffer big = new StringBuffer("WIRE1\nsome date\n");
        for (int i = 0; i < 25; i++) big.append("# item ").append(i).append("\nbody ").append(i).append("\n");
        Digest capped = Digest.parse(big.toString());
        check("hard cap at 10", capped.count() == Digest.MAX_ITEMS);
        check("cap keeps the FIRST ten", capped.item(0).headline.equals("item 0")
                && capped.item(9).headline.equals("item 9"));

        // --- CRLF tolerance + trailing-blank trim ---
        Digest crlf = Digest.parse("WIRE1\r\ndate\r\n# H\r\nbody\r\n\r\n");
        check("crlf date clean", crlf.date().equals("date"));
        check("crlf headline clean", crlf.item(0).headline.equals("H"));
        check("trailing blank trimmed", crlf.item(0).body.equals("body"));

        // --- Wrap: line widths respected, words preserved, blank line kept ---
        String para = "the quick brown fox jumps\n\nover the lazy dog";
        String[] wl = Wrap.lines(para, M, 10);
        boolean widthsOk = true;
        for (int i = 0; i < wl.length; i++) if (width(wl[i]) > 10 && wl[i].length() != 1) widthsOk = false;
        check("wrap widths <= max", widthsOk);
        check("wrap kept a blank line", hasEmpty(wl));
        check("wrap preserved words", join(wl).equals("thequickbrownfoxjumpsoverthelazydog"));

        System.out.println(fails == 0 ? "\nALL CHECKS PASS" : "\n" + fails + " FAILURES");
        if (fails != 0) System.exit(1);
    }

    static int width(String s) { return s.length(); }   // 1 unit/char
    static boolean hasEmpty(String[] a) {
        for (int i = 0; i < a.length; i++) if (a[i].length() == 0) return true;
        return false;
    }
    static String join(String[] a) {
        StringBuffer b = new StringBuffer();
        for (int i = 0; i < a.length; i++) {
            String s = a[i];
            for (int j = 0; j < s.length(); j++) if (s.charAt(j) != ' ') b.append(s.charAt(j));
        }
        return b.toString();
    }
    static void check(String name, boolean ok) {
        System.out.println((ok ? "ok   " : "FAIL ") + name);
        if (!ok) fails++;
    }
}
