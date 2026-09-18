package wire;

import javax.microedition.lcdui.*;

/**
 * Reading view for one headline. Wraps the item once into lines and scrolls a
 * window over them; moves between items; returns to the index. Baked light
 * theme, one font size -- no settings (tuned-to-me, like the rest of the suite).
 *
 * Keys (2/4/6/8 mirror the D-pad, 5 = OK):
 *   8 / down / 5 / fire = read on (page down); at the article's end, next item
 *   2 / up              = page up
 *   6 / right = next item        4 / left = previous item
 *   Back softkey = index
 * So 5/8 is "keep reading" and rolls to the next piece only when this one ends.
 * Advancing past the last item (or back before the first) returns to the index:
 * that wall is the point -- when the ten are read, there is nothing more.
 */
public final class WireCanvas extends Canvas implements CommandListener {

    private static final int MARGIN_X = 4, MARGIN_TOP = 2, FOOTER = 14;
    private static final int BG = 0xFFFFFF, FG = 0x101010, DIM = 0x999999, ACCENT = 0x1560B0;

    private final WireMIDlet mid;
    private final Digest digest;
    private int idx;

    private Font font, footFont;
    private int lineH;
    private String[] lines = new String[0];
    private int headCount = 0;   // leading lines that are the headline (drawn in ACCENT)
    private int top = 0;
    private String status = "";

    private final Command backCmd = new Command("Back", Command.BACK, 1);
    private final Command refreshCmd = new Command("Refresh", Command.SCREEN, 2);

    public WireCanvas(WireMIDlet mid, Digest digest, int idx) {
        this.mid = mid;
        this.digest = digest;
        this.idx = idx;
        setFullScreenMode(true);
        font = Font.getFont(Font.FACE_SYSTEM, Font.STYLE_PLAIN, Font.SIZE_MEDIUM);
        footFont = Font.getFont(Font.FACE_SYSTEM, Font.STYLE_PLAIN, Font.SIZE_SMALL);
        lineH = font.getHeight();
        build();
        addCommand(backCmd);
        addCommand(refreshCmd);
        setCommandListener(this);
    }

    private void build() {
        Item it = digest.item(idx);
        TextMeasure m = new FontMeasure(font);
        int w = maxWidth();
        String[] head = Wrap.lines(it.headline, m, w);
        String[] body = Wrap.lines(it.body, m, w);
        headCount = head.length;
        lines = new String[head.length + 1 + body.length];
        int p = 0;
        for (int i = 0; i < head.length; i++) lines[p++] = head[i];
        lines[p++] = "";
        for (int i = 0; i < body.length; i++) lines[p++] = body[i];
        top = 0;
        status = "";
    }

    private int maxWidth() { return getWidth() - 2 * MARGIN_X; }
    private int maxLines() {
        int usable = getHeight() - MARGIN_TOP - FOOTER;
        int n = usable / lineH;
        return (n < 1) ? 1 : n;
    }

    protected void paint(Graphics g) {
        g.setColor(BG);
        g.fillRect(0, 0, getWidth(), getHeight());
        g.setFont(font);
        int ml = maxLines();
        int y = MARGIN_TOP;
        for (int i = top; i < lines.length && i < top + ml; i++) {
            g.setColor(i < headCount ? ACCENT : FG);
            g.drawString(lines[i], MARGIN_X, y, Graphics.TOP | Graphics.LEFT);
            y += lineH;
        }
        g.setFont(footFont);
        g.setColor(DIM);
        String tag = digest.item(idx).tag;
        String left = (idx + 1) + "/" + digest.count() + (tag.length() > 0 ? "  " + tag : "");
        g.drawString(left, MARGIN_X, getHeight() - 1, Graphics.BOTTOM | Graphics.LEFT);
        String right = (status.length() > 0) ? status : scrollHint(ml);
        if (right.length() > 0) {
            g.drawString(right, getWidth() - MARGIN_X, getHeight() - 1,
                         Graphics.BOTTOM | Graphics.RIGHT);
        }
    }

    private String scrollHint(int ml) {
        if (lines.length <= ml) return "";
        if (top <= 0) return "more ↓";
        if (top + ml >= lines.length) return "↑ up";
        return "↑↓";
    }

    protected void keyPressed(int key) {
        int ga = 0;
        try { ga = getGameAction(key); } catch (Exception e) {}
        if (ga == Canvas.DOWN || key == KEY_NUM8 || ga == Canvas.FIRE || key == KEY_NUM5) readOn();
        else if (ga == Canvas.UP || key == KEY_NUM2) scroll(-1);
        else if (ga == Canvas.RIGHT || key == KEY_NUM6) nextItem();
        else if (ga == Canvas.LEFT || key == KEY_NUM4) prevItem();
    }

    /** Keep reading: page down while there's more of this article, then move on. */
    private void readOn() {
        if (canScrollDown()) scroll(1);
        else nextItem();
    }

    private boolean canScrollDown() {
        int ml = maxLines();
        return lines.length > ml && top < lines.length - ml;
    }

    private void scroll(int d) {
        int ml = maxLines();
        if (lines.length <= ml) return;                 // nothing to scroll
        int nt = top + d * (ml - 1);                    // page with one line of overlap
        if (nt > lines.length - ml) nt = lines.length - ml;
        if (nt < 0) nt = 0;
        if (nt != top) { top = nt; status = ""; repaint(); }
    }

    private void nextItem() {
        if (idx + 1 < digest.count()) { idx++; build(); repaint(); }
        else mid.showIndex();                           // past the last -> done
    }

    private void prevItem() {
        if (idx - 1 >= 0) { idx--; build(); repaint(); }
        else mid.showIndex();
    }

    public void commandAction(Command c, Displayable d) {
        if (c == backCmd) mid.showIndex();
        else if (c == refreshCmd) mid.refresh();
    }
}
