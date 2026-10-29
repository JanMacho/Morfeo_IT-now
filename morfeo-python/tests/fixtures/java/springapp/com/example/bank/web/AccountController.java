package com.example.bank.web;

import com.example.bank.dto.AccountDto;
import com.example.bank.dto.AccountStatus;
import com.example.bank.dto.BatchRequest;
import com.example.bank.dto.CreateAccountRequest;
import com.example.bank.dto.Movement;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Min;
import java.security.Principal;
import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Pageable;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.CookieValue;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.ValueConstants;

@RestController
@RequestMapping("/api/accounts")
public class AccountController {

    @GetMapping
    public List<AccountDto> list(@RequestParam(defaultValue = "0") @Min(0) int page,
                                 @RequestParam(name = "size", required = false, defaultValue = ValueConstants.DEFAULT_NONE) Integer size,
                                 @RequestParam Optional<String> owner) {
        return List.of();
    }

    @GetMapping("/{id:\\d+}")
    public ResponseEntity<AccountDto> get(@PathVariable long id, @RequestHeader("X-Request-Id") String requestId) {
        return ResponseEntity.ok(null);
    }

    @PostMapping(consumes = "application/json", produces = "application/json")
    public ResponseEntity<AccountDto> create(@Valid @RequestBody CreateAccountRequest request) {
        return ResponseEntity.ok(null);
    }

    @PostMapping("/batch")
    public List<AccountDto> batch(@RequestBody BatchRequest<CreateAccountRequest> batch) {
        return List.of();
    }

    @PatchMapping("/{id}/status")
    public AccountDto changeStatus(@PathVariable Long id, @RequestParam AccountStatus status) {
        return null;
    }

    @RequestMapping(value = "/{id}/movements", method = {RequestMethod.GET, RequestMethod.HEAD})
    public List<Movement> movements(@PathVariable("id") long accountId,
                                    @CookieValue(value = "session", required = false) String session) {
        return List.of();
    }

    @RequestMapping("/ping")
    public String ping() {
        return "pong";
    }

    @GetMapping("/search")
    public List<AccountDto> search(@RequestParam("q") String query, Pageable pageable,
                                   HttpServletRequest request, Principal principal) {
        return List.of();
    }

    @GetMapping("/export")
    public String export(double ratio, String format, boolean compressed) {
        return format;
    }

    @GetMapping("/static")
    public static String ignoredStatic() {
        return "static";
    }

    /** Metodo sin mapping. */
    public String helper() {
        return "helper";
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<String> onError(IllegalArgumentException e) {
        return ResponseEntity.ok(e.getMessage());
    }
}
