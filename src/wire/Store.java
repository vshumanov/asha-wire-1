package wire;

import javax.microedition.rms.RecordStore;

/**
 * A single RMS record holding the day's raw digest bytes. Small internal state,
 * survives exit/minimise/battery pull, needs no file permission -- the same
 * storage profile as Tally and Schedule. Each pull OVERWRITES the one record:
 * there is no yesterday, no archive, nothing to fall into.
 */
public final class Store {
    private static final String NAME = "wire";
    private String last = "(no store op yet)";

    public String getReport() { return last; }

    /** The stored digest bytes, or null if nothing has been pulled yet. */
    public byte[] load() {
        RecordStore rs = null;
        try {
            rs = RecordStore.openRecordStore(NAME, true);
            if (rs.getNumRecords() >= 1) {
                byte[] b = rs.getRecord(1);
                last = "load ok (" + (b == null ? 0 : b.length) + "B)";
                return b;
            }
            last = "empty";
            return null;
        } catch (Exception e) {
            last = "load err: " + e;
            return null;
        } finally {
            close(rs);
        }
    }

    /** Overwrite the one record with today's digest. */
    public void save(byte[] data) {
        RecordStore rs = null;
        try {
            rs = RecordStore.openRecordStore(NAME, true);
            if (rs.getNumRecords() == 0) rs.addRecord(data, 0, data.length);
            else rs.setRecord(1, data, 0, data.length);
            last = "save ok (" + data.length + "B)";
        } catch (Exception e) {
            last = "save err: " + e;
        } finally {
            close(rs);
        }
    }

    private static void close(RecordStore rs) {
        if (rs != null) try { rs.closeRecordStore(); } catch (Exception e) {}
    }
}
