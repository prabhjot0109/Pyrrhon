import { formatName } from "./helpers.js";

export class Greeter {
  greet(name: string): string {
    return formatName(name);
  }
}

export function main(): void {
  new Greeter().greet("world");
}
