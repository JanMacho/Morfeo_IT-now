package cf;

public record Point(@Nested(name = "x") int x, long y, String label) {
}
