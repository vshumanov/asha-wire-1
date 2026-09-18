package wire;

import javax.microedition.midlet.MIDlet;
import javax.microedition.lcdui.*;

/**
 * Wire -- the day's tech / retro-gaming / new-concepts headlines, pulled once
 * from the home server over the LAN and read offline the rest of the day.
 *
 * Flow: open -> the index (up to 10 headlines from the last pull, straight out
 * of RMS, no network needed) -> pick one to read -> back out. Refresh performs
 * the one deliberate LAN pull, overwriting yesterday. No feed, no archive, no
 * "load more": when the ten are read, there is nothing more.
 *
 * This is the suite's documented BEND of the "no network" rule (see the vault's
 * asha-philosophy): a single, user-initiated pull from the user's own server on
 * the user's own network -- not cloud, not an account, not a background feed.
 */
public class WireMIDlet extends MIDlet implements CommandListener {

    private Display display;
    private final Store store = new Store();
    private final Net net = new Net();
    private Digest digest;

    private List index;
    private Command readCmd, refreshCmd, diagCmd, exitCmd;

    protected void startApp() {
        display = Display.getDisplay(this);
        if (digest == null) {
            byte[] b = store.load();          // read yesterday-or-this-morning's pull, offline
            if (b != null) digest = Digest.parse(b);
        }
        showIndex();
    }

    protected void pauseApp() {}
    protected void destroyApp(boolean unconditional) {}

    /** The headline index -- the app's home. Built from whatever is in RMS. */
    public void showIndex() {
        String title = (digest != null && digest.date().length() > 0)
                ? ("Wire · " + digest.date()) : "Wire";
        index = new List(title, List.IMPLICIT);
        if (digest != null && digest.count() > 0) {
            for (int i = 0; i < digest.count(); i++) {
                Item it = digest.item(i);
                String label = (it.tag.length() > 0 ? "[" + it.tag + "] " : "") + it.headline;
                index.append(label, null);
            }
        } else {
            index.append("(empty – join wifi, then Refresh)", null);
        }
        readCmd = new Command("Read", Command.ITEM, 1);
        refreshCmd = new Command("Refresh", Command.SCREEN, 2);
        diagCmd = new Command("Diagnostics", Command.SCREEN, 3);
        exitCmd = new Command("Exit", Command.EXIT, 4);
        index.addCommand(readCmd);
        index.addCommand(refreshCmd);
        index.addCommand(diagCmd);
        index.addCommand(exitCmd);
        index.setCommandListener(this);
        display.setCurrent(index);
    }

    public void commandAction(Command c, Displayable d) {
        if (c == exitCmd) {
            destroyApp(true);
            notifyDestroyed();
        } else if (c == refreshCmd) {
            refresh();
        } else if (c == diagCmd) {
            Alert a = new Alert("Diagnostics",
                    "url: " + Net.URL + "\nnet: " + net.getReport()
                    + "\nstore: " + store.getReport()
                    + "\nitems: " + (digest == null ? 0 : digest.count()),
                    null, AlertType.INFO);
            a.setTimeout(Alert.FOREVER);
            display.setCurrent(a, index);
        } else if (c == readCmd || c == List.SELECT_COMMAND) {
            int i = index.getSelectedIndex();
            if (digest != null && i >= 0 && i < digest.count()) {
                display.setCurrent(new WireCanvas(this, digest, i));
            }
        }
    }

    /** The deliberate morning pull: GET over LAN, overwrite yesterday, rebuild index. */
    public void refresh() {
        try {
            byte[] b = net.fetch();
            Digest fresh = Digest.parse(b);
            if (fresh == null) { toast("Server sent a bad digest"); return; }
            store.save(b);
            digest = fresh;
            showIndex();
            toast("Updated – " + digest.count() + " on the wire");
        } catch (Exception e) {
            Alert a = new Alert("No pull",
                    "Couldn't reach the wire.\n" + net.getReport()
                    + "\n\nOn wifi? Home server up? Showing the last pull for now.",
                    null, AlertType.WARNING);
            a.setTimeout(Alert.FOREVER);
            display.setCurrent(a, (index != null ? (Displayable) index : new Form("Wire")));
        }
    }

    private void toast(String s) {
        Alert a = new Alert("Wire", s, null, AlertType.INFO);
        a.setTimeout(1200);
        display.setCurrent(a, index);
    }
}
