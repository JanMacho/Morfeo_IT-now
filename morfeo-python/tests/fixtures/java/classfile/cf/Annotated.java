package cf;

import java.util.List;
import java.util.function.Supplier;

@Everything(b = -8, c = 'Z', d = 2.5, f = 1.5f, i = 70000, j = 1234567890123L, s = -300, z = true,
        str = "a\u0000b😀", e = Color.GREEN, cls = String[].class,
        nested = @Nested(name = "inner", sizes = {1, 2}), colors = {Color.RED, Color.BLUE})
public class Annotated<T extends Comparable<T>> {
    public static final long BIG = 9876543210123L;
    public static final double RATIO = 3.25;
    public static final String AFTER = "after-long";

    private List<? extends Number> numbers;
    protected T value;

    @Nested(name = "method")
    public long mix(long a, double b, @Nested(name = "p") String c) {
        long x = a + 1234567890123L;
        double y = b * 6.02e23;
        return x + (long) y + c.length();
    }

    public int dense(int k) {
        switch (k) {
            case 0: return 10;
            case 1: return 11;
            case 2: return 12;
            case 3: return 13;
            case 4: return 14;
            default: return -1;
        }
    }

    public int sparse(int k) {
        switch (k) {
            case 1: return 1;
            case 1000: return 2;
            case 100000: return 3;
            default: return 0;
        }
    }

    public int wide(int start) {
        int i = start;
        i += 1000;
        i -= 20000;
        return i;
    }

    public Supplier<String> lambda(String prefix) {
        return () -> prefix + AFTER;
    }

    public <R> R generic(Class<R> type, List<? super T> sink) {
        return null;
    }
}
