package com.acme.notify;

import com.acme.notify.EmailSender;

/** Listens for order events and notifies the customer. */
public class OrderEventListener {
    private final EmailSender emailSender;

    public OrderEventListener(EmailSender emailSender) {
        this.emailSender = emailSender;
    }

    public void onOrderPlaced(String customerEmail, String orderId) {
        emailSender.send(customerEmail, "Order confirmed", "Your order " + orderId + " was placed.");
    }
}
