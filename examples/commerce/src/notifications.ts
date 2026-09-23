import { subscribe } from "./bus";

export function notifyCustomer() {
  subscribe("order.created", sendEmail);
}

function sendEmail() { return "sent"; }
