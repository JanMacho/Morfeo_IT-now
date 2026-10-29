package com.example.bank.web;

import com.example.bank.dto.Customer;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.validation.BindingResult;
import org.springframework.web.bind.WebDataBinder;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.InitBinder;
import org.springframework.web.bind.annotation.ModelAttribute;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.multipart.MultipartFile;

/** Controlador de formularios al estilo de Spring PetClinic. */
@Controller
@RequestMapping("/customers")
public class CustomerFormController {

    @ModelAttribute("regions")
    public List<String> regions() {
        return List.of("north", "south");
    }

    @InitBinder
    public void initBinder(WebDataBinder binder) {
        binder.setDisallowedFields("id");
    }

    @GetMapping("/new")
    public String initCreationForm(Model model) {
        return "customers/form";
    }

    @PostMapping("/new")
    public String processCreationForm(@Valid Customer customer, BindingResult result) {
        return result.hasErrors() ? "customers/form" : "redirect:/customers";
    }

    @GetMapping("/find")
    public String processFindForm(@RequestParam(defaultValue = "1") int page, Customer customer,
                                  BindingResult result, Model model) {
        return "customers/list";
    }

    @PostMapping("/{customerId}/edit")
    public String processUpdateForm(@Valid @ModelAttribute("customer") Customer customer, BindingResult result,
                                    @PathVariable("customerId") int customerId) {
        return "redirect:/customers/" + customerId;
    }

    @PostMapping(value = "/upload", consumes = "multipart/form-data")
    public String upload(@RequestParam("file") MultipartFile file, @RequestParam("description") String description) {
        return "ok";
    }
}
